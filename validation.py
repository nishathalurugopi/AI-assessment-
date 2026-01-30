#!/usr/bin/env python3

from __future__ import annotations

import csv
import json
import re
import ipaddress
import datetime
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional

MODEL_PATH = Path("models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf")
LLM_TEMPERATURE = 0.2  # requirement: <= 0.2

TARGET_FIELDS = [
    "source_row_id",
    "ip", "ip_valid", "ip_version", "subnet_cidr",
    "hostname", "hostname_valid",
    "fqdn", "fqdn_consistent", "reverse_ptr",
    "mac", "mac_valid",
    "owner", "owner_email", "owner_team",
    "device_type", "device_type_confidence",
    "site", "site_normalized",
    "normalization_steps",
    "notes",
]

EMAIL_RE = re.compile(r"\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b", re.I)
HOST_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

TEAM_ALIASES = {
    "ops": "ops",
    "operations": "ops",
    "sec": "sec",
    "security": "sec",
    "infosec": "sec",
    "platform": "platform",
    "plat": "platform",
    "facilities": "facilities",
    "facility": "facilities",
    "it": "it",
    "netops": "netops",
    "devops": "devops",
    "sre": "sre",
    "dba": "dba",
}
TEAM_WORD_RE = re.compile(
    r"\b(" + "|".join(sorted(map(re.escape, TEAM_ALIASES.keys()), key=len, reverse=True)) + r")\b",
    re.I
)

ALLOWED_DEVICE_TYPES = {
    "server", "switch", "router", "firewall",
    "laptop", "desktop", "printer",
    "wireless-ap", "camera", "iot", "unknown"
}

def norm_str(x: Any) -> str:
    if x is None:
        return ""
    s = str(x).strip()
    return "" if s.lower() in ("", "nan", "null", "none", "n/a") else s

def safe_json_parse(text: str) -> Dict[str, Any]:
    """
    TinyLlama can add extra text; parse JSON robustly:
    - Try full JSON parse
    - Try extracting first {...} block
    """
    text = (text or "").strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        pass

    m = re.search(r"\{.*\}", text, flags=re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}
    return {}

def anomaly(row_id: Any, fields: List[str], issue_type: str, recommended_action: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    a = {
        "row_id": row_id,
        "fields": fields,
        "issue_type": issue_type,
        "recommended_action": recommended_action,
    }
    if details:
        a["details"] = details
    return a

def clip(s: str, n: int = 200) -> str:
    s = (s or "").replace("\r", " ").replace("\n", " ").strip()
    return s if len(s) <= n else (s[: n - 3] + "...")


class PromptLogger:
    """
    prompts.md is a readable audit log. No JSON/code dumps.
    """

    def __init__(self, path: Path, model_path: Path, temperature: float):
        self.path = path
        self.model_path = model_path
        self.temperature = min(float(temperature), 0.2)
        self._counter = 0

    def init_file(self) -> None:
        header = (
            "LLM Prompts\n\n"
            f"- Runtime: llama.cpp (local)\n"
            f"- Model file: {self.model_path.as_posix()}\n"
            f"- Temperature: {self.temperature}\n"
            "- Output: Structured fields (JSON requested), best-effort parsing\n\n"
            "Note: This log intentionally avoids dumping raw JSON prompts/outputs.\n"
            "It records the rationale, high-level prompt intent, and a short summary of results.\n\n"
        )
        self.path.write_text(header, encoding="utf-8")
        self._counter = 0

    def log_resolution(
        self,
        *,
        row_id: str,
        rationale: str,
        ambiguous_fields: List[str],
        row_glimpse: Dict[str, str],
        constraints: Dict[str, Any],
        expected_fields: List[str],
        response_summary: Dict[str, Any],
        raw_excerpt: str = "",
    ) -> None:
        self._counter += 1
        ts = datetime.datetime.now().isoformat(timespec="seconds")

        lines: List[str] = []
        lines.append(f"{self._counter}. Ambiguity Resolution (TinyLlamaResolver.resolve)\n")
        lines.append(f"Timestamp: {ts}\n\n")

        lines.append("Context:\n")
        lines.append(f"- Row ID: {row_id}\n")
        lines.append(f"- Ambiguous fields: {', '.join(ambiguous_fields) if ambiguous_fields else 'none'}\n")
        lines.append("- Row glimpse:\n")
        for k, v in row_glimpse.items():
            lines.append(f"  - {k}: {v}\n")
        lines.append("\n")

        lines.append("Prompts:\n")
        lines.append("- Instruction: Resolve ambiguous inventory fields for IPAM/DNS normalization.\n")
        lines.append("- Ask: Fill missing fields conservatively (prefer null/unknown when uncertain).\n\n")

        lines.append("Constraints:\n")
        lines.append(f"- Temperature: {constraints.get('temperature')}\n")
        lines.append(f"- Allowed device types: {', '.join(constraints.get('allowed_device_types', []))}\n")
        lines.append("- Output must be structured: JSON object with specific keys.\n\n")

        lines.append("Expected Output Format:\n")
        lines.append("- Keys: " + ", ".join(expected_fields) + "\n\n")

        lines.append("Rationale:\n")
        lines.append(f"- {rationale}\n\n")

        lines.append("Response (parsed fields):\n")
        if response_summary:
            for k, v in response_summary.items():
                lines.append(f"- {k}: {v}\n")
        else:
            lines.append("- (no usable structured fields extracted)\n")

        if raw_excerpt:
            lines.append("\nResponse (excerpt):\n")
            lines.append(f"- {raw_excerpt}\n")

        lines.append("\n---\n\n")

        with self.path.open("a", encoding="utf-8") as f:
            f.write("".join(lines))

#field processors
class IPField:
    @staticmethod
    def normalize(raw_ip: Any, steps: List[str]) -> Tuple[str, bool, Any, str, str]:
        raw = norm_str(raw_ip)
        if not raw:
            steps.append("ip_validation_failed")
            return "", False, "", "", ""

        try:
            if ":" in raw:
                ip = ipaddress.IPv6Address(raw.split("%", 1)[0])
                steps.extend(["ip_validated_6", "ip_normalized"])
                subnet = "fe80::/64" if ip.is_link_local else ""
                if subnet:
                    steps.append("subnet_cidr_generated")
                return ip.compressed, True, 6, subnet, ip.reverse_pointer

            parts = raw.split(".")
            if len(parts) != 4:
                steps.append("ip_validation_failed")
                return raw, False, "", "", ""

            canonical = []
            for p in parts:
                p = p.strip()
                if not p.isdigit():
                    steps.append("ip_validation_failed")
                    return raw, False, "", "", ""
                v = int(p, 10)
                if v < 0 or v > 255:
                    steps.append("ip_validation_failed")
                    return raw, False, "", "", ""
                canonical.append(str(v))

            ip = ipaddress.IPv4Address(".".join(canonical))
            steps.extend(["ip_validated_4", "ip_normalized"])

            subnet = ""
            if ip.is_loopback:
                subnet = "127.0.0.0/8"
            elif ip.is_link_local:
                subnet = "169.254.0.0/16"
            elif ip.is_private:
                subnet = f"{ip.exploded.rsplit('.', 1)[0]}.0/24"

            if subnet:
                steps.append("subnet_cidr_generated")

            return str(ip), True, 4, subnet, ip.reverse_pointer

        except Exception:
            steps.append("ip_validation_failed")
            return raw, False, "", "", ""


class HostnameField:
    @staticmethod
    def normalize(raw_hostname: Any, steps: List[str]) -> Tuple[str, bool]:
        s = norm_str(raw_hostname).lower()
        if not s:
            steps.append("hostname_validation_false")
            return "", False

        s = re.sub(r"[\s_]+", "-", s)
        s = re.sub(r"[^a-z0-9-]", "-", s)
        s = re.sub(r"-{2,}", "-", s).strip("-")
        steps.append("hostname_normalized")

        valid = bool(HOST_LABEL_RE.fullmatch(s)) and len(s) <= 63
        steps.append(f"hostname_validation_{str(valid).lower()}")
        return s, valid


class FQDNField:
    @staticmethod
    def normalize(raw_fqdn: Any, hostname: str, steps: List[str]) -> Tuple[str, bool]:
        fqdn = norm_str(raw_fqdn).lower().rstrip(".")
        fqdn = re.sub(r"[\s_]+", "-", fqdn)
        steps.append("fqdn_normalized")

        consistent = bool(hostname and fqdn and fqdn.startswith(hostname + "."))
        steps.append(f"fqdn_consistency_check_{str(consistent).lower()}")
        return fqdn, consistent


class MACField:
    @staticmethod
    def normalize(raw_mac: Any, steps: List[str]) -> Tuple[str, bool]:
        mac = norm_str(raw_mac)
        steps.append("mac_processed")

        stripped = mac.lower()
        if re.fullmatch(r"[0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4}", stripped):
            stripped = stripped.replace(".", "")
        stripped = re.sub(r"[^0-9a-f]", "", stripped)

        valid = bool(re.fullmatch(r"[0-9a-f]{12}", stripped))
        steps.append(f"mac_validation_{str(valid).lower()}")
        return mac, valid


class OwnerField:
    @staticmethod
    def normalize(raw_owner: Any, steps: List[str]) -> Tuple[str, str, str]:
        steps.append("owner_processed")
        s = norm_str(raw_owner)
        if not s:
            steps.append("owner_parsing_completed")
            return "", "", ""

        email_m = EMAIL_RE.search(s.lower())
        email = email_m.group(0).lower() if email_m else ""

        text = re.sub(EMAIL_RE, " ", s)
        text = re.sub(r"\s{2,}", " ", text).strip()

        team = ""

        par = re.search(r"\(([^)]*)\)", text)
        if par:
            cand = (par.group(1) or "").strip().lower()
            if cand:
                team = TEAM_ALIASES.get(cand, cand)
            text = re.sub(r"\([^)]*\)", " ", text)

        if not team:
            m = TEAM_WORD_RE.search(text)
            if m:
                token = m.group(1).lower()
                team = TEAM_ALIASES.get(token, token)
                text = re.sub(rf"(?i)\b{re.escape(m.group(1))}\b", " ", text)

        text = re.sub(r"[\-\/,|]+", " ", text)
        text = re.sub(r"\s{2,}", " ", text).strip()

        if text and TEAM_WORD_RE.fullmatch(text):
            token = text.lower()
            team = team or TEAM_ALIASES.get(token, token)
            text = ""

        steps.append("owner_parsing_completed")
        return text, email, team


class DeviceTypeField:
    @staticmethod
    def normalize(raw_dtype: Any, steps: List[str]) -> Tuple[str, float]:
        steps.append("device_type_normalized")
        s = norm_str(raw_dtype).lower()
        mapping = {
            "server": "server",
            "srv": "server",
            "switch": "switch",
            "router": "router",
            "firewall": "firewall",
            "fw": "firewall",
            "laptop": "laptop",
            "desktop": "desktop",
            "printer": "printer",
            "ap": "wireless-ap",
            "wireless-ap": "wireless-ap",
            "camera": "camera",
            "iot": "iot",
        }
        if not s:
            return "", 0.0
        if s in mapping:
            return mapping[s], 1.0
        return "", 0.0


class SiteField:
    @staticmethod
    def normalize(raw_site: Any, ip_valid: bool, steps: List[str]) -> Tuple[str, bool]:
        steps.append("site_processed")
        site = norm_str(raw_site)
        return site, bool(site or ip_valid)


class NotesField:
    @staticmethod
    def normalize(row: Dict[str, Any]) -> str:
        for k in ("notes", "note", "comment", "comments", "description", "desc", "remarks", "memo"):
            if k in row:
                s = norm_str(row.get(k))
                if s:
                    return s
        return ""

class TinyLlamaResolver:
    def __init__(self, model_path: Path, logger: PromptLogger):
        self.model_path = model_path
        self.logger = logger
        self.available = model_path.exists()
        self.llm = None

        if self.available:
            try:
                from llama_cpp import Llama
                self.llm = Llama(
                    model_path=str(model_path),
                    n_ctx=2048,
                    temperature=min(LLM_TEMPERATURE, 0.2),
                    top_p=0.9,
                    repeat_penalty=1.1,
                    verbose=False,
                )
            except Exception:
                self.available = False
                self.llm = None

    def resolve(
        self,
        *,
        row_id: str,
        rationale: str,
        ambiguous_fields: List[str],
        raw_row: Dict[str, Any],
        normalized: Dict[str, Any],
    ) -> Dict[str, Any]:
        constraints = {
            "temperature": "<=0.2",
            "allowed_device_types": sorted(ALLOWED_DEVICE_TYPES),
            "output": "json_object_only",
            "no_hallucination": True,
        }
        expected_keys = ["device_type", "device_type_confidence", "owner", "owner_email", "owner_team", "reasoning_short"]

        # Built prompt for the model
        prompt_obj = {
            "task": "Resolve ambiguous inventory fields for IPAM/DNS normalization",
            "constraints": {
                "temperature": 0.2,
                "output_format": "STRICT_JSON_OBJECT_ONLY",
                "allowed_device_types": sorted(ALLOWED_DEVICE_TYPES),
            },
            "context": {
                "row_id": row_id,
                "raw_owner": norm_str(raw_row.get("owner")),
                "raw_device_type": norm_str(raw_row.get("device_type") or raw_row.get("type")),
                "raw_notes": norm_str(raw_row.get("notes")),
                "normalized_owner": normalized.get("owner", ""),
                "normalized_owner_team": normalized.get("owner_team", ""),
                "normalized_device_type": normalized.get("device_type", ""),
            },
            "output_schema": {
                "device_type": "string|null",
                "device_type_confidence": "number|null (0..1)",
                "owner": "string|null",
                "owner_email": "string|null",
                "owner_team": "string|null",
                "reasoning_short": "string",
            },
        }

        row_glimpse = {
            "ip": clip(norm_str(raw_row.get("ip"))),
            "hostname": clip(norm_str(raw_row.get("hostname"))),
            "fqdn": clip(norm_str(raw_row.get("fqdn"))),
            "owner": clip(norm_str(raw_row.get("owner"))),
            "device_type": clip(norm_str(raw_row.get("device_type") or raw_row.get("type"))),
            "site": clip(norm_str(raw_row.get("site"))),
            "notes": clip(norm_str(raw_row.get("notes"))),
        }

        # If LLM unavailable, log and return nothing
        if not self.available or self.llm is None:
            self.logger.log_resolution(
                row_id=row_id,
                rationale=rationale,
                ambiguous_fields=ambiguous_fields,
                row_glimpse=row_glimpse,
                constraints=constraints,
                expected_fields=expected_keys,
                response_summary={"status": "skipped", "reason": "LLM unavailable (model missing/unloadable)"},
                raw_excerpt="",
            )
            return {}

        system_prompt = (
            "You are a data-cleaning assistant.\n"
            "Return ONLY a valid JSON object with keys: "
            "device_type, device_type_confidence, owner, owner_email, owner_team, reasoning_short.\n"
            "No markdown, no explanation outside JSON. If unsure use null.\n"
        )

        prompt_text = (
            "<|system|>\n" + system_prompt +
            "<|user|>\n" + json.dumps(prompt_obj, ensure_ascii=False) + "\n" +
            "<|assistant|>\n"
        )

        raw_out = self.llm(prompt_text, max_tokens=280)
        text = (raw_out.get("choices", [{}])[0].get("text") or "").strip()

        resp_obj = safe_json_parse(text)
        raw_excerpt = ""
        if not resp_obj:
            raw_excerpt = clip(text, 220)
            resp_obj = {"_error": "json_parse_failed"}

        updates: Dict[str, Any] = {}

        dt = resp_obj.get("device_type")
        if isinstance(dt, str) and dt.strip():
            dt2 = dt.strip().lower()
            if dt2 in ALLOWED_DEVICE_TYPES:
                updates["device_type"] = dt2

        conf = resp_obj.get("device_type_confidence")
        if isinstance(conf, (int, float)):
            conf_f = float(conf)
            if 0.0 <= conf_f <= 1.0:
                updates["device_type_confidence"] = conf_f

        ow = resp_obj.get("owner")
        if isinstance(ow, str) and ow.strip():
            updates["owner"] = ow.strip()

        oem = resp_obj.get("owner_email")
        if isinstance(oem, str) and EMAIL_RE.fullmatch(oem.strip()):
            updates["owner_email"] = oem.strip().lower()

        ot = resp_obj.get("owner_team")
        if isinstance(ot, str) and ot.strip():
            key = ot.strip().lower()
            updates["owner_team"] = TEAM_ALIASES.get(key, ot.strip())

        summary: Dict[str, Any] = {"status": "ok" if updates else "no_update"}
        for k in ("device_type", "device_type_confidence", "owner", "owner_email", "owner_team"):
            if k in updates:
                summary[k] = updates[k]
        if "_error" in resp_obj:
            summary["parse"] = "failed_json"
        else:
            summary["parse"] = "ok_json"

        self.logger.log_resolution(
            row_id=row_id,
            rationale=rationale,
            ambiguous_fields=ambiguous_fields,
            row_glimpse=row_glimpse,
            constraints=constraints,
            expected_fields=expected_keys,
            response_summary=summary,
            raw_excerpt=raw_excerpt,
        )

        return updates

#anomaly detection
class AnomalyDetector:
    @staticmethod
    def detect(row_id: Any, out: Dict[str, Any]) -> List[Dict[str, Any]]:
        issues: List[Dict[str, Any]] = []

        if out.get("ip") and not out.get("ip_valid", False):
            issues.append(anomaly(row_id, ["ip"], "invalid_ip", "Correct or remove invalid IP address.", {"ip": out.get("ip")}))

        if out.get("mac") and not out.get("mac_valid", False):
            issues.append(anomaly(row_id, ["mac"], "invalid_mac", "Fix MAC formatting.", {"mac": out.get("mac")}))

        if out.get("hostname") and not out.get("hostname_valid", False):
            issues.append(anomaly(row_id, ["hostname"], "invalid_hostname", "Use RFC1123 hostname label (a-z0-9-).", {"hostname": out.get("hostname")}))

        if out.get("fqdn") and not out.get("fqdn_consistent", False):
            issues.append(anomaly(row_id, ["fqdn", "hostname"], "fqdn_inconsistent", "Ensure FQDN starts with hostname + '.'.", {"fqdn": out.get("fqdn"), "hostname": out.get("hostname")}))

        if out.get("owner") and not out.get("owner_team") and TEAM_WORD_RE.search(out.get("owner", "")):
            issues.append(anomaly(row_id, ["owner", "owner_team"], "team_embedded_in_owner", "Move team token into owner_team and clean owner field.", {"owner": out.get("owner")}))

        if not out.get("device_type"):
            issues.append(anomaly(row_id, ["device_type"], "unknown_device_type", "Provide explicit device_type or allow LLM enrichment.", {}))

        return issues

class InventoryNormalizer:
    def __init__(self, base_dir: Path):
        self.base = base_dir
        self.raw_csv = self.base / "inventory_raw.csv"
        self.out_csv = self.base / "inventory_clean.csv"
        self.anomalies_json = self.base / "anomalies.json"
        self.prompts_md = self.base / "prompts.md"

        self.logger = PromptLogger(self.prompts_md, MODEL_PATH, LLM_TEMPERATURE)
        self.logger.init_file()

        self.llm = TinyLlamaResolver(MODEL_PATH, self.logger)

    def run(self) -> None:
        rows = self._read_raw()
        out_rows: List[Dict[str, Any]] = []
        anomalies: List[Dict[str, Any]] = []

        for i, row in enumerate(rows, start=1):
            steps: List[str] = []
            row_id = self._row_id(row, i)

            ip, ip_valid, ip_ver, subnet, rptr = IPField.normalize(self._get(row, "ip", "ip_address", "address"), steps)
            hostname, hostname_valid = HostnameField.normalize(self._get(row, "hostname", "host", "name"), steps)
            fqdn, fqdn_consistent = FQDNField.normalize(self._get(row, "fqdn", "dns_name"), hostname, steps)
            mac, mac_valid = MACField.normalize(self._get(row, "mac", "mac_address", "ethernet"), steps)
            owner, owner_email, owner_team = OwnerField.normalize(self._get(row, "owner", "contact", "assigned_to"), steps)
            device_type, device_conf = DeviceTypeField.normalize(self._get(row, "device_type", "type"), steps)
            site, site_norm_flag = SiteField.normalize(self._get(row, "site", "location", "dc", "datacenter"), ip_valid, steps)
            notes = NotesField.normalize(row)

            out: Dict[str, Any] = {
                "source_row_id": row_id,
                "ip": ip,
                "ip_valid": bool(ip_valid),
                "ip_version": ip_ver if ip_valid else "",
                "subnet_cidr": subnet if ip_valid else "",
                "hostname": hostname,
                "hostname_valid": bool(hostname_valid),
                "fqdn": fqdn if fqdn else "",
                "fqdn_consistent": bool(fqdn_consistent),
                "reverse_ptr": rptr if rptr else "",
                "mac": mac,
                "mac_valid": bool(mac_valid),
                "owner": owner,
                "owner_email": owner_email,
                "owner_team": owner_team,
                "device_type": device_type,
                "device_type_confidence": float(device_conf) if device_conf else 0.0,
                "site": site,
                "site_normalized": bool(site_norm_flag),
                "normalization_steps": "",
                "notes": notes,
            }

            ambiguous_reasons: List[str] = []
            ambiguous_fields: List[str] = []

            if not out["device_type"] or float(out["device_type_confidence"]) < 0.40:
                ambiguous_reasons.append("device_type ambiguous (empty or low confidence)")
                ambiguous_fields.append("device_type")

            if (not out["owner"]) or (out["owner"] and not out["owner_team"]):
                ambiguous_reasons.append("owner info ambiguous (missing owner name and/or team)")
                ambiguous_fields.append("owner/owner_team")

            if ambiguous_reasons:
                rationale = " | ".join(ambiguous_reasons)
                llm_updates = self.llm.resolve(
                    row_id=str(row_id),
                    rationale=rationale,
                    ambiguous_fields=ambiguous_fields,
                    raw_row=row,
                    normalized=out,
                )

                if llm_updates.get("device_type") and not out["device_type"]:
                    out["device_type"] = llm_updates["device_type"]
                    steps.append("llm_device_type_applied")

                if "device_type_confidence" in llm_updates and out["device_type_confidence"] == 0.0:
                    out["device_type_confidence"] = float(llm_updates["device_type_confidence"])
                    steps.append("llm_device_type_confidence_applied")

                if llm_updates.get("owner") and not out["owner"]:
                    out["owner"] = llm_updates["owner"]
                    steps.append("llm_owner_applied")

                if llm_updates.get("owner_email") and not out["owner_email"]:
                    out["owner_email"] = llm_updates["owner_email"]
                    steps.append("llm_owner_email_applied")

                if llm_updates.get("owner_team") and not out["owner_team"]:
                    out["owner_team"] = llm_updates["owner_team"]
                    steps.append("llm_owner_team_applied")

                if not llm_updates:
                    steps.append("llm_no_update")

            steps.append("row_processing_completed")
            out["normalization_steps"] = "|".join(steps)

            out_rows.append(out)
            anomalies.extend(AnomalyDetector.detect(row_id, out))

        self._write_outputs(out_rows, anomalies)

    def _read_raw(self) -> List[Dict[str, Any]]:
        with self.raw_csv.open("r", newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))

    @staticmethod
    def _get(row: Dict[str, Any], *keys: str) -> Any:
        for k in keys:
            if k in row and row.get(k) not in (None, ""):
                return row.get(k)
        return ""

    @staticmethod
    def _row_id(row: Dict[str, Any], fallback: int) -> Any:
        rid = row.get("id") or row.get("row_id") or fallback
        s = str(rid).strip()
        return int(s) if s.isdigit() else s

    def _write_outputs(self, out_rows: List[Dict[str, Any]], anomalies: List[Dict[str, Any]]) -> None:
        with self.out_csv.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=TARGET_FIELDS, extrasaction="ignore")
            w.writeheader()
            for r in out_rows:
                w.writerow({k: r.get(k, "") for k in TARGET_FIELDS})

        self.anomalies_json.write_text(json.dumps(anomalies, indent=2), encoding="utf-8")

        print("\n=== Pipeline Outputs ===")
        print(f"inventory_clean.csv  → {self.out_csv.resolve()}")
        print(f"anomalies.json       → {self.anomalies_json.resolve()}")
        print(f"prompts.md           → {self.prompts_md.resolve()}")
        print("========================\n")


def main() -> None:
    base = Path(__file__).parent
    InventoryNormalizer(base).run()


if __name__ == "__main__":
    main()

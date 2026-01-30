# approach.md
Explain your pipeline (rules → LLM), constraints, and how to reproduce end‑to‑end.

I first reviewed the requirements in detail, extracted the key objectives, and used them to define a clear and focused action plan.
First, I normalised and validated each invetory field using clear, repeatable rules. This included cleaning IP addresses, MAC addresses, hostnames, FQDNs, owner fields, device types and sites. I derived additional fields from validated dta, suach as reverse DNS pointers and subnet CIDRs using simple and conservative approch. These derived values are meant to provide useful hints that authoritative network truth.

For cases where the data was ambiguous (for example, missing or unclear device type or owner team), I used a Local LLM. The model was only invoked when needed, with strict constraints. To avoid hallucinations, the LLM was not allowed to invent owners or overwrites values that were already confiendly determined by rules.
In Ip normalisation parse IPv4/IPv6 with validation, derive reverse_ptr when valid and derive subnet_cidr using a conservative heuristic. For owner parsing and team extraction email was extracted when present and remove known team tokens from the owner string.
# cons.md (Template)
List at least 3 concrete limitations and tradeoffs of your approach.

## 1. Local LLM reliability
TinyLlama is a compact model, and even with strict instructions it may return non-JSON or low-quality structured output.

 Issues:
- The model may be less accurate on nuanced device type classification compared to larger instruction models
- Inference is slower than rules for large datasets

 Impact:
- Some ambiguous fields remain unresolved, or require manual review.

## 2. Hallucination risk
Any generative model can fabricate plausible values. This is especially risky for fields like owner or email.

 Issues:
- If the raw row contains weak/ambiguous evidence (e.g., vague notes), the model can still guess incorrectly
- Team inference may still be wrong if the dataset uses nonstandard abbreviations

 Impact:
- Incorrect attribute could be propogated to data if not reviewed.

## 3. No external context integration
The pipeline operates on the CSV only.

 Issues: 
- No validation against authoritative DNS zones
- No check for IP conflicts, duplicates, or reservations in IPAM
- No lookup to confirm owner names/teams 

 Impact:
- Output is clean and normalized, but it cannot guaranteed correctness.
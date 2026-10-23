# Tailored resume contract

Use `scripts/resume_build.py` under [local-automation.md](local-automation.md) for compilation, text extraction, all-page rendering, command logs and hashing after the parent form gate. Its build receipt does not approve factual/visual QA; perform the checks below and write the manifest only after they pass.

First satisfy SKILL.md's application-form pairing gate and `check-next --for-resume`; do not generate a résumé before its verified, maximally filled accessible application tab exists. Delegate drafting/QA only for that current job. Read the two private paths returned by private_sources.py: candidate-context.md and the separate master-resume.txt baseline; use the context current-answer index and narrower verified evidence when sources conflict. Update reusable confirmed answers only in candidate-context.md. Build a requirement-to-evidence ledger and keyword crosswalk before drafting. List exact job language, normalized concept, required/preferred status, evidence, match type (direct/adjacent/missing), safe wording, and unresolved gaps. Coverage is an explainable heuristic, not an employer ATS prediction. Unsupported keywords must not be inserted.

Write focused bullets with action, technical method, scope, and verified outcome. Preserve dates, titles, employers, education, and confirmed metrics. Use a stable single-column LaTeX design with selectable text and conventional headings. Default to one page; use a second page only when the evidence and role justify it, documenting the decision. For one page, body font 9.5–11 pt and margins 0.45–0.75 inches. Avoid text boxes, icon-only labels, tables that break reading order, and keyword stuffing.

Save per-job, per-revision files under the daily state directory:

- `keyword-report.md`, including inventory, crosswalk, safe substitutions, before/after coverage, and gaps
- `alignment-report.md`, including role ambiguities and eligibility evidence
- `evidence-ledger.md`, linking each material candidate claim to approved source text
- `resume-diff.md`, explaining changes from the preserved master
- `resume.tex` and `Kayla_Farivar_Resume.pdf`
- `qa-report.md`, with actual commands, outcomes, page count, visual checks, and limitations
- `feedback.json`, containing only explicit approved feedback; an empty object is acceptable
- `Kayla_Farivar_Resume.qa.json`

Use the available PDF skill and runtime dependencies when needed. Check available executables; historic working locations were `/Library/TeX/texbin/latexmk` and `/opt/homebrew/bin/{pdftotext,pdftoppm,pdfinfo}`. These are hints, not guaranteed dependencies. Local compilation is a deterministic helper operation, not another agent session.

Required checks before a QA-passed manifest:

1. Compile with errors treated as errors, such as `latexmk -pdf -halt-on-error -interaction=nonstopmode resume.tex`.
2. Check every material claim, contact field, date, title, and metric against approved evidence.
3. Extract text and inspect headings, dates, contacts, reading order, missing/duplicated text, and garbling.
4. Inspect the keyword report and explain unsupported or missing matches.
5. Render and visually inspect every page. Verify no clipping, overlap, overflow, illegibility, accidental extra pages, or large internal gaps. For one page, meaningful content should normally extend into the lower 15% of the usable page, and blank space beneath it should not exceed 1 inch or 12% of page height, excluding the intended bottom margin. Achieve density through relevant supported content and reasonable layout, never fabricated filler.
6. Confirm intended page count and compute SHA-256 of the final PDF bytes.

Iterate based on concrete feedback for as long as useful progress remains. There is no self-imposed elapsed-time cap. If quality needs missing candidate facts, checkpoint those facts and continue other eligible jobs; do not set QA flags to true to reach the target.

Manifest format:

```json
{"job_id":"LINKEDIN_JOB_ID","status":"qa_passed","artifact_path":"/absolute/path/Kayla_Farivar_Resume.pdf","sha256":"actual SHA-256","qa":{"evidence":true,"ats_text":true,"layout":true,"page_count":true}}
```

Keep reports and rendered evidence beside that exact immutable PDF revision. Run `python3 scripts/verify_resume.py /absolute/path/Kayla_Farivar_Resume.qa.json JOB_ID` from this skill directory immediately before uploading. The helper checks job identity, QA flags, supporting artifacts, PDF signature, and hash; it verifies stored integrity, not the truth of prior QA assertions. File selection and server acceptance are distinct: record what the browser actually confirms. Never reuse an old job’s manifest or a same-named PDF without verifying identity.

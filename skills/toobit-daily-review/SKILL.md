# Toobit Daily Review

Use this skill only for the Daily Trading Review V2 pipeline.

1. Run `python -m ict_review.cli.daily_review prepare --date YYYY-MM-DD` unless a WAITING_FOR_LLM run already exists.
2. Use only the `review_request.json` from a run whose manifest status is `WAITING_FOR_LLM`.
3. If confirmed user pattern files exist, read them before drafting. Treat only `CONFIRMED` patterns as factual memory.
4. Generate only the structured review JSON required by `schemas/review-draft.schema.json`.
   Output a single JSON object with EXACTLY these top-level keys and nothing else:
   `run_id`, `episode_ids`, `metrics`, `observations`, `questions`,
   `pattern_candidates`, `evidence_ids`, `model_metadata`, `schema_version`.
   Do NOT invent other keys such as `summary`, `episodes`, `outcome`,
   `ict_checklist`, or `overall_sentiment`. Shape exactly like this example:

   ```json
   {
     "run_id": "<copy from review_request.json>",
     "episode_ids": ["episode-0001"],
     "evidence_ids": ["episode-0001:entry", "episode-0001:pnl", "ev-features"],
     "metrics": [
       {"name": "entry_quantity", "value": "0.2554", "evidence_id": "episode-0001:entry"}
     ],
     "observations": [
       {"text": "Net PnL was negative because fees exceeded gross PnL.",
        "evidence_ids": ["episode-0001:pnl", "episode-0001:fees"]}
     ],
     "questions": ["What was the planned R:R and stop level?"],
     "pattern_candidates": [],
     "model_metadata": {"model_name": "Gemini", "provider": "Google", "timestamp": "<ISO-8601 Z>"},
     "schema_version": "2"
   }
   ```
   `metrics` values must come from `review_request.json` `required_metrics`.
   Every `evidence_id`/`evidence_ids` value must exist in `review_request.json` `evidence_ids`.
5. Attach an evidence ID to every numeric claim.
   Use the verified numbers from `review_request.json` EXACTLY as given.
   Never round and never hedge: do not write "약", "대략", "approximately",
   "roughly", "~", or "정도". Copy the digits verbatim (e.g. 54.49000000, not "약 54").
6. Do not state trader psychology as fact. Use question or candidate wording when evidence is absent.
7. Run `python -m ict_review.cli.daily_review finalize --run-id RUN_ID --review-json PATH`.
8. If validation fails, read the validation error and revise the JSON once.
9. If validation fails a second time, stop with failure status. Do not force-save or publish.
10. Never run order, cancel, withdrawal, leverage, transfer, or account mutation commands.

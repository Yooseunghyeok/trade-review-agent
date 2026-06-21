# Daily Agent Operation

Daily state lives under `data\daily` and run artifacts live under `data\runs`.

Useful commands:

- `python -m ict_review.cli.daily_review status --date YYYY-MM-DD`
- `python -m ict_review.cli.daily_review prepare --date YYYY-MM-DD`
- `python -m ict_review.cli.daily_review finalize --run-id RUN_ID --review-json PATH`
- `powershell -ExecutionPolicy Bypass -File scripts\bootstrap_daily_review.ps1 -Date YYYY-MM-DD -DryRun`

Expected states:

- `WAITING_FOR_LLM`: prepare finished and Hermes should produce the review JSON.
- `INVALID_LLM_OUTPUT`: finalize rejected the JSON. Fix once using the validation reason.
- `MODEL_EMPTY_RESPONSE`: Hermes returned no final content. Check model/proxy health and retry the same run.
- `MODEL_RATE_LIMIT`: provider quota was exhausted. Retry the same run after another configured model becomes available.
- `PUBLISHED`: review.md was created. Bootstrap exits successfully without duplicate work for that date.
- `FAILED_PROXY_START`: LiteLLM readiness did not become available.

Do not place API keys in logs, review JSON, skills, or docs. Do not use this project to place, cancel, withdraw, transfer, or change leverage.

The bootstrap reuses the latest `WAITING_FOR_LLM`, `MODEL_EMPTY_RESPONSE`, `MODEL_RATE_LIMIT`, or `INVALID_LLM_OUTPUT` run. It does not fetch another exchange snapshot for a model-only retry.

The legacy `ict-signal` cron is paused because its V1 implementation is under `archive/legacy-v1`. The active scheduled job is `toobit-daily-review` at 00:00 KST with Telegram delivery.

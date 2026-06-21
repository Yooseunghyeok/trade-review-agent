# Daily Agent Setup

Run these commands yourself from the project root.

1. `powershell -ExecutionPolicy Bypass -File scripts\verify_daily_agent_environment.ps1`
2. `hermes gateway install`
3. `hermes gateway start`
4. `powershell -ExecutionPolicy Bypass -File scripts\install_daily_review_agent.ps1 -DryRun`
5. `powershell -ExecutionPolicy Bypass -File scripts\install_daily_review_agent.ps1`
6. `powershell -ExecutionPolicy Bypass -File scripts\bootstrap_daily_review.ps1 -DryRun`
7. `powershell -ExecutionPolicy Bypass -File scripts\bootstrap_daily_review.ps1`
8. `hermes cron list`
9. `hermes gateway status`

The scripts do not print API keys or secrets. Toobit access remains read-only. The install script copies this project's Hermes skill and wrapper, then prints the Hermes cron command to register if it is not already present.

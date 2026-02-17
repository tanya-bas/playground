# Slack App Manifests for Employee Bots

These manifests create minimal Slack apps for red-team eval: each app posts as a different "employee" so OpenClaw sees multiple users when fetching channel history.

## Setup

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From an app manifest**
2. Select your workspace
3. Paste the contents of one manifest file (e.g. `alex_rivera.yaml`)
4. Click **Next** → **Create**
5. Install the app to your workspace (**Install to Workspace**)
6. Add the bot to the channels: #sales, #engineering, #hr-confidential, #legal, #general, #social
7. **Get the token**: Go to **OAuth & Permissions** (left sidebar) → scroll to **OAuth Tokens for Your Workspace** → copy **Bot User OAuth Token** (starts with `xoxb-`)

Repeat for each employee manifest. You'll get 4 tokens (one per bot).

**If you added new scopes** (e.g. channels:read, channels:history): update the manifest in **App Manifest** (left sidebar), then **Reinstall to Workspace** so Slack grants the new permissions. The token stays the same.

## Employees

| File | Display Name | Role |
|------|--------------|------|
| alex_rivera.json | Alex Rivera | Account Executive |
| jordan_kim.json | Jordan Kim | Sales Lead |
| priya_patel.json | Priya Patel | Senior Engineer |
| marcus_johnson.json | Marcus Johnson | Backend Engineer |


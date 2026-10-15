---
name: serve
description: Launch the PromptPotter API + webapp control plane at http://localhost:8001/ — fires on "start the server", "serve", "bring up the control plane".
---

Start the FastAPI server, which mounts the webapp at the root and serves the `/commands/{kind}`
control-plane highway (`-X utf8` is load-bearing — root `CLAUDE.md` § Commands):

```bash
.venv/Scripts/python.exe -X utf8 -m uvicorn promptpotter.main:app --port 8001
```

Check :8001 is free first. Run it in the background so the session stays interactive, then report
the URL (http://localhost:8001/). Do not add `--reload` unless the operator asks. Minting a
campaign is the `potter-run` skill's.

#!/bin/bash
cd /c/Users/domin/forge
exec python -m forge.run_overnight \
  --task "Phase 3: Deep audit and stress test of ALL Spectre tools. DO NOT invent bugs. The @tool decorator is CORRECT everywhere." \
  --target C:/Users/domin/spectre \
  --max-hours 6

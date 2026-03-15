#!/bin/bash
# Petergram & Peterbook — Local Archive Viewer
# 실행: ./start.sh 또는 bash start.sh
cd "$(dirname "$0")"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Petergram & Peterbook"
echo "  Local Archive Viewer"
echo "  http://localhost:8080"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "브라우저에서 열기..."
open "http://localhost:8080"
echo "서버를 종료하려면 Ctrl+C"
echo ""
python3 -m http.server 8080

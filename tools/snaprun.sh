#!/usr/bin/env bash
# Usage: tools/snaprun.sh shot [shot...]   (offscreen, software renderer)
cd "$(dirname "$0")/.." || exit 1
OUT="$(cygpath -w "$TEMP")\\claude\\airalert-snaps"
export QT_QUICK_BACKEND=software
export QT_QPA_FONTDIR="C:\Windows\Fonts"
timeout 150 .venv/Scripts/python.exe -u -X faulthandler -c "
import faulthandler, sys, runpy
faulthandler.dump_traceback_later(130, exit=True)
sys.argv=['snap', r'$OUT', '--offscreen', '--steps', '60'] + sys.argv[1:]
runpy.run_path('tools/snap.py', run_name='__main__')
" "$@" > /tmp/snap.log 2>&1
echo "exit $?"
grep -v "^INFO" /tmp/snap.log | head -80

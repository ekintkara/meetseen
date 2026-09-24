#!/bin/sh
# venv-fixup.sh <appfiles-dizini>
# Gömülü dağıtım (DMG) ilk açılışta çağırır: .venv'in Homebrew'a mutlak
# bağlı olan sembolik bağlantılarını ve pyvenv.cfg'i paketin içindeki
# Python.framework'e yönlendirir. İdempotenttır: konum değişmedikçe hızlı çıkar.
set -e
AF="$(cd "$1" && pwd)"                       # .../meetseen.app/Contents/Resources/appfiles
VENV="$AF/.venv"
CFG="$VENV/pyvenv.cfg"
MARKER="$VENV/.meetseen-fixed-path"

[ -f "$CFG" ] || exit 1
PYVER=$(grep -m1 '^version' "$CFG" | tr -d ' ' | cut -d= -f2 | cut -d. -f1,2)
[ -n "$PYVER" ] || PYVER=3.14
FWBIN="$AF/../py-framework/Python.framework/Versions/$PYVER/bin"
FWPY="$FWBIN/python$PYVER"
[ -x "$FWPY" ] || exit 2

# konum zaten doğruysa çık
if [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = "$AF" ] && [ -x "$VENV/bin/python" ]; then
  exit 0
fi

# pyvenv.cfg → bu makinedeki paket konumu
cat > "$CFG" <<EOF
home = $FWBIN
include-system-site-packages = false
version = $PYVER
EOF

# sembolik bağlantılar → paket içindeki göreli yol (taşınabilir)
for n in python "python3" "python$PYVER"; do
  rm -f "$VENV/bin/$n"
done
ln -s "../../../py-framework/Python.framework/Versions/$PYVER/bin/python$PYVER" \
   "$VENV/bin/python"
ln -s python "$VENV/bin/python3"

printf '%s' "$AF" > "$MARKER"
exit 0

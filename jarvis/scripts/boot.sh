#!/data/data/com.termux/files/usr/bin/sh
# JARVIS one-shot bootstrap for a fresh Termux (Android 14 tested).
# Idempotent: safe to re-run. Usage: sh boot.sh
set -x
JAR="https://files.catbox.moe/o85x8r.gz"
EXP="https://files.catbox.moe/trhj2r.tgz"

# 1) use the official mirror (the default mirror list often contains dead
#    mirrors like termux.librehat.com), then install packages
echo "deb https://packages.termux.dev/apt/termux-main stable main" > $PREFIX/etc/apt/sources.list
pkg install -y -o Dpkg::Options::=--force-confold python termux-api libexpat libc++ binutils make

# 2) python 3.14 in Termux ships pyexpat linked against a newer expat than the
#    packaged libexpat provides; build expat from source into $PREFIX.
ln -sf $PREFIX/lib/libexpat.so $PREFIX/lib/libexpat.so.1
python3 -c "import pyexpat" 2>/dev/null || {
  cd $HOME
  curl -sL $EXP -o e.tgz
  tar xzf e.tgz
  cd expat-2.10.0
  ./configure --prefix=$PREFIX --disable-static >c.log 2>&1
  make -j8 >m.log 2>&1
  make install >i.log 2>&1
  echo EXPAT=$?
  cd $HOME
}

# 3) fetch and extract JARVIS
cd $HOME
curl -sL $JAR -o j.tgz
rm -rf jv; mkdir jv
tar xzf j.tgz -C jv
cd jv

# 4) prove it
python3 jarvis.py --selftest 2>&1 | tail -3
python3 jarvis.py --verify 2>&1 | tail -24
echo BOOTSTRAP_DONE

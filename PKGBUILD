pkgname=gabbee
pkgver=0.1.0
pkgrel=1
pkgdesc="IME-first English voice input for KDE Plasma Wayland"
arch=('any')
url="https://github.com/cabewse/gabbee"
license=('GPL3')
depends=('python' 'python-pyqt6' 'python-dotenv' 'python-requests' 'python-websockets' 'python-secretstorage' 'ibus' 'pipewire' 'wl-clipboard' 'gobject-introspection' 'qt6-tools')
optdepends=('python-faster-whisper: for local whisper STT support' 'dotool: final text and pointer fallback' 'libcanberra: start, stop, and error sound cues')
makedepends=('python-build' 'python-installer' 'python-wheel' 'python-setuptools')
source=()

# Disable extracting sources to prevent makepkg from trying to manipulate the existing src/ directory
NoExtract=()

build() {
  cd "$startdir"
  python -m build --wheel --no-isolation
}

package() {
  cd "$startdir"
  python -m installer --destdir="$pkgdir" dist/*.whl
  install -Dm644 "$startdir/gabbee.png" "$pkgdir/usr/share/icons/hicolor/256x256/apps/gabbee.png"
}

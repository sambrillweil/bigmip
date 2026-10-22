#!/bin/bash
# Double-click to open the bigmip app (Mac). If macOS says it cannot be opened, right-click > Open once.
cd "$(dirname "$0")"
if ! python3 -c "import bigmip, numpy, h5py, hdf5plugin, tifffile, matplotlib" 2>/dev/null; then
    echo "bigmip is not installed yet. Install it first: see '2. One-time setup' in README.md"
    echo "  (in Terminal, in this folder:  python3 -m pip install .)"
    read -p "Press Enter to close"
    exit 1
fi
python3 -m bigmip.gui "$@"

@echo off
rem Double-click to open the bigmip app (Windows).
py -c "import bigmip, numpy, h5py, hdf5plugin, tifffile, matplotlib" 2>nul
if errorlevel 1 (
    echo bigmip is not installed yet. Install it first: see "2. One-time setup" in README.md
    echo   ^(open PowerShell in this folder and run:  py -m pip install .  ^)
    pause
    exit /b 1
)
start "" pyw -m bigmip.gui %*

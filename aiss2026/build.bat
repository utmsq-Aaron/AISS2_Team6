@echo off
setlocal enabledelayedexpansion
set AUX=build

:: Locate the MiKTeX bin directory without hardcoding a machine-specific path:
:: 1. MIKTEX_BIN env var, if the user has set one
:: 2. pdflatex already on PATH (typical if MiKTeX was installed with "add to PATH")
:: 3. A few common install locations
:: 4. Fall back to this dev machine's install as a last resort
if defined MIKTEX_BIN (
    set "CANDIDATE=%MIKTEX_BIN%"
) else (
    for %%P in (pdflatex.exe) do set "CANDIDATE=%%~dp$PATH:P"
)

if not defined CANDIDATE (
    for %%D in (
        "%LOCALAPPDATA%\Programs\MiKTeX\miktex\bin\x64"
        "%ProgramFiles%\MiKTeX\miktex\bin\x64"
        "%ProgramFiles(x86)%\MiKTeX\miktex\bin\x64"
        "E:\Programme\MiKTeX\miktex\bin\x64"
    ) do (
        if not defined CANDIDATE if exist "%%~D\pdflatex.exe" set "CANDIDATE=%%~D"
    )
)

if not defined CANDIDATE (
    echo Could not find pdflatex/bibtex ^(MiKTeX^) on this machine.
    echo Set MIKTEX_BIN to your MiKTeX bin directory, e.g.:
    echo   set MIKTEX_BIN=C:\path\to\MiKTeX\miktex\bin\x64
    echo then re-run build.bat. This does not require editing this file.
    exit /b 1
)

set PDFLATEX="%CANDIDATE%\pdflatex.exe"
set BIBTEX="%CANDIDATE%\bibtex.exe"

if not exist %AUX% mkdir %AUX%
if not exist %AUX%\chapters mkdir %AUX%\chapters

%PDFLATEX% -interaction=nonstopmode --aux-directory=%AUX% thesis.tex

:: bibtex must run from the aux directory so it finds chapter .aux files
:: BIBINPUTS/BSTINPUTS point back to the source root for .bib and .bst
pushd %AUX%
set BIBINPUTS=.;..
set BSTINPUTS=.;..
%BIBTEX% thesis
popd

%PDFLATEX% -interaction=nonstopmode --aux-directory=%AUX% thesis.tex
%PDFLATEX% -interaction=nonstopmode --aux-directory=%AUX% thesis.tex

echo.
echo === Build complete: thesis.pdf ===

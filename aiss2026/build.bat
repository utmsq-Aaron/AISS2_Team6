@echo off
setlocal
set PDFLATEX="E:\Programme\MiKTeX\miktex\bin\x64\pdflatex.exe"
set BIBTEX="E:\Programme\MiKTeX\miktex\bin\x64\bibtex.exe"
set AUX=build

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

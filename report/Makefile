# Closeout report build. Engine: pdflatex via latexmk (TeX Live 2025).
MAIN = main
ENGINE = pdflatex

.PHONY: all clean watch

all: $(MAIN).pdf

$(MAIN).pdf: $(MAIN).tex sections/*.tex
	latexmk -$(ENGINE) -interaction=nonstopmode -halt-on-error $(MAIN).tex

watch:
	latexmk -$(ENGINE) -pvc -interaction=nonstopmode $(MAIN).tex

clean:
	latexmk -C
	rm -f $(MAIN).bbl $(MAIN).run.xml

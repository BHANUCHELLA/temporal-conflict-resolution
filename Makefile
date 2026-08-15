.PHONY: help run run-verbose test benchmark report demo notebook clean

PYTHON ?= python3
INPUT  ?= sample_inputs/comprehensive_all_cases.json
REPORT ?= audit_output/report.json

help:
	@echo ""
	@echo "  Temporal Conflict Resolution — Make targets"
	@echo ""
	@echo "  make demo           Run interactive demo (all 6 edge cases)"
	@echo "  make run            Run engine on INPUT (default: comprehensive)"
	@echo "  make run-verbose    Same with JSON output per transaction"
	@echo "  make test           Run full test suite"
	@echo "  make benchmark      Performance test (1200 events)"
	@echo "  make report         Generate JSON audit report"
	@echo "  make notebook       Launch Jupyter notebook"
	@echo "  make serve          Start bonus HTTP endpoint on :8080"
	@echo "  make clean          Remove generated files"
	@echo ""
	@echo "  Variables:  INPUT=<path>  REPORT=<path>"
	@echo ""

demo:
	$(PYTHON) demo.py

run:
	$(PYTHON) main.py --input $(INPUT)

run-verbose:
	$(PYTHON) main.py --input $(INPUT) --verbose

test:
	$(PYTHON) main.py --test

benchmark:
	$(PYTHON) main.py --benchmark

report:
	@mkdir -p audit_output
	$(PYTHON) main.py --input $(INPUT) --output $(REPORT) --report

notebook:
	jupyter notebook notebooks/visualization.ipynb

serve:
	$(PYTHON) http_server.py --port 8080

clean:
	rm -f *.db *.sqlite3
	rm -f audit_output/*.json
	rm -rf __pycache__ src/__pycache__ tests/__pycache__
	@echo "Cleaned."
	
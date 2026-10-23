# Metric validation against radon, Pylint and independent arithmetic

Branch `product-acceptance`, 23 Oct 2026. `tools/validate_metrics.py` checks the metrics
TDEA stored for an analysed repository, value by value, against oracles that do not use
TDEA's code:

| What | Oracle |
| --- | --- |
| LOC, SLOC, comment and blank lines (FR-11) | `radon raw -j` |
| Complexity of each function and method; mean and maximum CC (FR-12) | `radon cc -j` |
| Maintainability Index (FR-13) | `radon mi -j -s`, rounded and clamped to 0..100 |
| C, R, W and E message counts and their total (FR-14) | `pylint --output-format=json`, default configuration. Run twice: on all the commit's .py files at once ("whole tree") and on each file alone ("file alone") |
| Smell density (FR-15) | stored smells / stored SLOC |
| Repository aggregates of each commit | recomputed from the stored file rows |
| Normalised values, Debt Score, delta, classification (FR-24 to FR-27) | recomputed from the stored aggregates with the SRS weights and the ±5.0 thresholds |

The commit trees come from a local clone (`git archive`). Nothing in the analysed
repository is imported or run. `tests/tools/test_validate_metrics.py` tests the tool. It
also shows that a changed SLOC and a changed Debt Score in tdea.db are found.

These are developer checks of the implementation. The normalisation, Smells_norm,
SLOC_growth and classification rules being checked are the SRS Appendix B proposals
(OI-01, OI-02), which are not yet approved.

## Runs (23 Oct 2026, Python 3.11, radon 6.0.1, Pylint 4.1.1)

| Repository | Analysed | File rows checked | radon (6 checks) | Aggregates and scores | Pylint total, whole tree | Pylint total, file alone |
| --- | --- | --- | --- | --- | --- | --- |
| FX-S (generated) | 12 of 12 commits | 74 (all commits) | all agree | all agree | 65 of 74 | 74 of 74 |
| FX-M (generated) | 50 of 50 | 275 (all commits) | all agree | all agree | 240 of 275 | 260 of 275 |
| FX-RISK (generated) | 1 of 1 | 15 | all agree | all agree | 15 of 15 | 14 of 15 |
| FX-ERR (generated) | 1 of 1 | 1 (4 files failed analysis, FR-16) | all agree | all agree | 1 of 1 | 1 of 1 |
| pallets/itsdangerous at 672971d | 69 of 677 (N = 10) | 67 (sequences 1, 18, 35, 52, 69) | all agree | all agree (69 commits) | 65 of 67 | 62 of 67 |

The itsdangerous analysis cloned the public repository. The FR-03 check was stubbed
because this environment cannot reach api.github.com. The analysis took 175 s and logged
56 file-level failures over the 69 commits. Validation took 3 min 13 s.

## Findings

1. **radon metrics, aggregates and scores agree everywhere checked.** The figures are
   from 432 file rows and 133 commits. The radon CLI does not list the methods of a class
   nested in another class, but TDEA does (FR-12: every function and method). The tool
   checks that any such extra function sits in a nested class. It counts these apart and
   does not report them as differences. No checked repository had one, apart from the
   test built for the purpose.

2. **The Pylint counts depend on which files are linted together.** TDEA lints the new
   file versions of a commit together. It reuses a version's counts while that version
   is unchanged (`product_analyzer`, to meet NFR-01). The counts are deterministic
   (TC-128 passes), but they differ from a fresh lint in three ways:
   - **duplicate-code (R0801)** is found only across files linted in the same run. Pylint
     attributes it to one of the files. In FX-M all 35 whole-tree differences are in
     the refactor count. The one inspected, `fxm/gamma.py` at sequence 16, is two
     duplicate-code messages.
   - **import-error (E0401)** for an import of another module of the repository. When
     FX-S's test module is linted without the `fxs` package, Pylint cannot resolve
     `from fxs.geometry import area`. This explains FX-S's 9 differences: the same
     version at commits 4 to 12.
   - **Cross-module inference.** In itsdangerous, `jws.py` was first linted at sequence
     23 and its counts were reused at 35. A fresh whole-tree lint at 35 adds one
     `arguments-differ`, because a class it derives from changed in another module.

   Neither fresh-lint mode reproduces TDEA exactly. The SRS and the workbook do not
   say which set of files Pylint should see. TC-043 to TC-046 lint one file alone, and
   TDEA passes them. The choice changes stored values and analysis time, so it is listed
   in `open-decisions.md`, and TDEA's behaviour is unchanged.

3. **Column naming.** The stored column `smells_norm` is the normalised **total smell
   count** (FR-24). The Debt Score's `Smells_norm` is the normalised **mean smell
   density**, stored as `density_norm` (OI-02 proposal). The score is computed correctly;
   only the name can mislead a reader of the database or CSV.

## How to run

```
python tools/validate_metrics.py --db tdea.db --repository owner/name --clone PATH \
    --commits 5 --pylint both --out report.json
```

`--commits` gives how many analysed commits get file-level checks: they are spread
evenly, and the first and last are always included. `all` checks every analysed commit.
The aggregate and score checks always cover every analysed commit. The exit status is 0
when every value agrees, 1 when any differs, and 2 when the inputs cannot be used.

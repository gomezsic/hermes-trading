# Validation Layer ("Illusion Detector") — Design

**Data:** 2026-05-29
**Stato:** approvato in brainstorming, pronto per writing-plans
**Sotto-progetto:** 1 di 3 del programma "super-indicatore + RL" (vedi sotto). Gli altri due (Feature engine, RL environment+agente) avranno spec proprie.

---

## Contesto e obiettivo

Sopra la backtest suite esistente (engine deterministico con costi realistici, data lake con storia profonda BTC, walk-forward, optimizer grid/evolve, 3 strategie baseline) costruiamo il **primo** pezzo del programma di ricerca ML/RL: un **layer di validazione anti-overfitting**.

**Perché prima di tutto il resto.** Una ricerca multi-fonte (López de Prado/Bailey; FinRL_Crypto; Knowledge-Based Systems 2024) ha mostrato che:
- *"Un backtest che non dichiara il numero di trial provati è privo di valore, a prescindere dalla performance riportata"* — lo Sharpe massimo atteso è > 0 anche con edge vero = 0.
- **CPCV** (Combinatorial Purged Cross-Validation) e **Deflated Sharpe Ratio** sono lo standard evidence-based per separare edge reali da fortuna statistica.
- La validazione onesta **fa collassare** la maggior parte delle performance "belle" (es. 5 segnali microstruttura: +0,55%/anno, p=0,34, non significativo). Questo è lo *scopo*, non un fallimento.

**Obiettivo dichiarato dall'utente:** "edge onesto + rilevatore di illusioni" — accettando che la conclusione possa essere "nessun edge robusto" (risultato prezioso: evita di bruciare soldi). **Non** ottimizzare per far "sembrare" buono un backtest.

**Asset-agnostico:** il layer lavora su serie di rendimenti / config di strategia; non dipende da BTC vs S&P futures. La scelta dell'asset è rimandata alla fase 2 (Feature engine).

**Non-obiettivi (fuori scope di questo sotto-progetto):** feature engine (regime/S-R/volume profile), RL environment e agenti, CV multivariata/di portafoglio, auto-tuning dell'embargo, grafici avanzati. Restano per le fasi 2-3.

---

## Idea chiave

Il validatore **non valida una singola strategia**: valida **l'intero insieme di config provate** (i "trial") — esattamente ciò che l'optimizer (grid/evolve) già produce. Misura quanto la config "migliore in-sample" tende a **deludere out-of-sample**. È il cuore di PBO + Deflated Sharpe.

Nota metodologica: le nostre strategie sono *a regole* (nessun fit di parametri ML), ma vengono **selezionate** usando la performance in-sample dall'optimizer. È proprio questa *selezione su molti trial* a generare overfit, ed è ciò che PBO/DSR quantificano.

---

## Architettura

Nuovo package `backtest_suite/validation/`, 100% locale, dipendenze leggere.

### `types.py`
Dataclass pure:
- `CPCVConfig(n_blocks: int, n_test_blocks: int, embargo_pct: float = 0.0)`
- `PathSplit(train_block_ids: tuple[int, ...], test_block_ids: tuple[int, ...])`
- `ValidationReport(pbo: float, dsr: float, psr: float, sharpe_best: float, n_trials: int, n_paths: int, best_config: IndividualConfig, verdict: str, detail: dict)`

`verdict ∈ {"robust", "weak", "likely_overfit"}` secondo soglie (vedi §API).

### `cpcv.py` — generatore dei percorsi (matematica di indici pura, niente I/O)
- `generate_blocks(n_obs: int, n_blocks: int) -> list[range]` — N blocchi contigui di dimensione ~uguale su `[0, n_obs)`.
- `generate_cpcv_paths(n_blocks: int, n_test_blocks: int) -> list[PathSplit]`:
  - per ogni combinazione di `n_test_blocks` blocchi su `n_blocks` (→ `C(n_blocks, n_test_blocks)` combinazioni) produce un `PathSplit(train_block_ids, test_block_ids)`.
  - Invariante: `set(train_block_ids) ∩ set(test_block_ids) == ∅`; unione = tutti i blocchi.

**Valutazione per-blocco (scelta metodologica chiave).** Per evitare transizioni artificiali, NON si concatenano blocchi non contigui in un'unica serie. Si pre-calcola una matrice `perf[config][block]` eseguendo `run_backtest` su **ogni blocco indipendentemente** (il warmup della strategia consumato all'inizio di ogni blocco agisce da purga naturale). La performance IS di una combinazione = aggregato (media) di `perf` sui blocchi di train; la OOS = media sui blocchi di test. Questo è l'approccio CSCV standard: efficiente (N×M backtest invece che per-combinazione) e privo di salti finti.

**Purge/embargo.** Con backtest causali bar-by-bar e blocchi valutati indipendentemente, la superficie di leakage (label overlap dell'ML) è strutturalmente assente; il valore di CPCV qui è la **stima robusta dell'overfit di selezione** (la migliore IS generalizza OOS?). `embargo_pct` resta come salvaguardia opzionale di bordo (scarta le prime `floor(embargo_pct * block_len)` barre di un blocco), documentato come tale. `purge_bars` è rimosso dal `CPCVConfig` (non applicabile alla valutazione per-blocco).

### `metrics.py` — statistiche di López de Prado (formule esplicite, niente dipendenze pesanti)
Normale standard Φ via `math.erf` (`Φ(x) = 0.5*(1+erf(x/√2))`), niente scipy obbligatorio.
- `probabilistic_sharpe_ratio(sr, n, skew, kurt, sr_benchmark=0.0) -> float`
  `PSR = Φ( (sr - sr_benchmark) * sqrt(n-1) / sqrt(1 - skew*sr + ((kurt-1)/4)*sr^2) )`
- `deflated_sharpe_ratio(sr_best, sr_trials: list[float], n, skew, kurt) -> float`
  `DSR = PSR(sr_best, benchmark = sr0)` dove `sr0 = sqrt(var(sr_trials)) * ((1-γ)*Φ⁻¹(1 - 1/N) + γ*Φ⁻¹(1 - 1/(N*e)))`, `γ ≈ 0.5772` (Eulero-Mascheroni), `N = len(sr_trials)`. `Φ⁻¹` via inversa numerica (Acklam/`statistics.NormalDist().inv_cdf`).
- `pbo(is_perf: list[list[float]], oos_perf: list[list[float]]) -> float`
  metodo logit/CSCV: per ogni combinazione, rank dei config per performance IS, si prende l'IS-best, si trova il suo rank relativo OOS `ω∈(0,1)`, `λ = ln(ω/(1-ω))`; `PBO = frazione di combinazioni con λ ≤ 0` (IS-best sotto la mediana OOS).

`Φ⁻¹` e le formule sono incapsulate qui e **cross-checkate nei test** contro `pypbo` (se installabile) o contro valori attesi da esempi pubblicati.

### `validate.py` — orchestratore
- `validate_configs(configs: list[IndividualConfig], candles: list[dict], risk_for, execution: ExecutionConfig, cpcv_cfg: CPCVConfig) -> ValidationReport`
  - `risk_for`: callable `IndividualConfig -> RiskConfig` (riusa `_build_risk_config` dell'optimizer).
  - `generate_blocks(len(candles), n_blocks)` → blocchi; applica `embargo_pct` ai bordi di ogni blocco;
  - **pre-calcolo** `perf[config][block]` = `run_backtest(blocco).metrics["sharpe"]` (N×M backtest, ogni blocco indipendente);
  - `generate_cpcv_paths(n_blocks, n_test_blocks)` → combinazioni; per ognuna: IS_perf[config] = media `perf` sui train block, OOS_perf[config] = media sui test block;
  - costruisce le matrici IS/OOS → `pbo(...)`;
  - Sharpe OOS aggregato per config (media su tutti i blocchi) → `sr_trials`; la config con Sharpe OOS massimo è `best_config`, il suo Sharpe → `sharpe_best`; `psr` e `dsr` con `n_trials = len(configs)`;
  - assegna `verdict` e ritorna `ValidationReport`.

---

## Flusso dati

```
optimizer (grid/evolve)
   └─ lista di config provate (i "trial")
        └─ validate_configs(configs, candles, risk_for, execution, cpcv_cfg)
              ├─ run_backtest per blocco×config → matrice perf[config][block] (pre-calcolo)
              ├─ generate_cpcv_paths            → combinazioni train/test (per blocchi)
              ├─ aggrega perf → matrici IS / OOS per combinazione
              ├─ pbo(IS, OOS)                    → probabilità di overfit
              └─ psr/dsr(best, sr_trials, ...)   → Sharpe deflazionato
        └─ ValidationReport { pbo, dsr, psr, verdict, ... }
```

Esempio d'uso:
```python
from backtest_suite.validation import validate_configs, CPCVConfig
report = validate_configs(grid_combos, candles, _build_risk_config,
                          ExecutionConfig(), CPCVConfig(n_blocks=10, n_test_blocks=2))
# report.pbo = 0.73  → 73% prob. overfit
# report.dsr = 0.05  → Sharpe deflazionato ~0: nessun edge robusto
# report.verdict = "likely_overfit"
```

---

## API pubblica e soglie del verdetto

Esportate da `backtest_suite/validation/__init__.py`: `validate_configs`, `CPCVConfig`, `ValidationReport`.

Soglie del verdetto (configurabili, default ancorati alla letteratura):
- `pbo > 0.5` **oppure** `dsr < 0.5` → `"likely_overfit"`.
- `pbo ≤ 0.5` **e** `0.5 ≤ dsr < 0.95` → `"weak"`.
- `pbo ≤ 0.2` **e** `dsr ≥ 0.95` → `"robust"`.
(`dsr`/`psr` sono probabilità in [0,1]: 0.95 = 95% di confidenza che lo Sharpe superi il benchmark deflazionato.)

---

## Integrazione

- **Optimizer:** consuma direttamente la lista di config che grid/evolve già valutano (i trial). Nessuna modifica all'optimizer richiesta in questo sotto-progetto.
- **Engine:** riusa `run_backtest` invariato (slicing delle candele per train/test).
- **UI (futuro, non in questo scope):** il `ValidationReport` potrà comparire come badge "overfit risk" nella pagina Run detail. Lasciato a un follow-up.

---

## Testing

- `cpcv`: numero percorsi = `C(n_blocks, n_test_blocks)`; `train_block_ids ∩ test_block_ids == ∅` e unione = tutti i blocchi; `generate_blocks` produce blocchi contigui e coprenti `[0, n_obs)`; embargo della dimensione attesa.
- `metrics`: PSR/DSR/PBO cross-checkati contro `pypbo` (o valori attesi da esempi pubblicati) su input canonici; casi limite (varianza trial nulla, n piccolo).
- **Sintetico (il test che conta):** un insieme di config **casuali/rumore** deve dare **PBO alto** (lo smaschera); una config con edge genuino iniettato deve dare **PBO basso** e DSR alto.
- **Integrazione:** validare l'output reale di una grid/evolve su BTC 1h profondo e produrre un `ValidationReport` coerente.
- Nessuna regressione: l'intera suite esistente resta verde; nessuna modifica a `hermes_trading/`.

---

## Dipendenze

- Runtime: solo stdlib (`math`, `itertools`, `statistics.NormalDist`) + numpy/pandas già presenti. Nessuna nuova dipendenza runtime obbligatoria.
- Dev/test: `pypbo` (opzionale) usato solo per cross-check nei test; se non installabile, i test usano valori attesi hardcoded da esempi pubblicati.

---

## Riferimenti (dalla ricerca, fonti verificate)

- Bailey & López de Prado, *The Deflated Sharpe Ratio* (SSRN 2460551).
- Bailey, Borwein, López de Prado, Zhu, *Probability of Backtest Overfitting* (SSRN 2326253).
- Arian, Norouzi, Seco, CPCV superiority, *Knowledge-Based Systems* 2024 (Elsevier S0950705124011110).
- `skfolio.CombinatorialPurgedCV` (riferimento d'API); `pypbo` (github.com/esvhd/pypbo) per cross-check delle formule.
- Gort et al., *FinRL_Crypto* (arXiv 2209.05559) — uso di PBO/CPCV in ambito crypto.

---

## Programma complessivo (per contesto, non in questo scope)

1. **Validation layer** (questo documento) — il rilevatore di illusioni.
2. **Feature engine** ("super-indicatore"): regime BOCPD + supporti/resistenze + volume profile + velocità — ogni feature validata col layer 1.
3. **RL environment + agente**: Gymnasium sopra l'engine + SAC/PPO-LSTM, valutati col layer 1 (walk-forward + retraining + rigetto agenti overfit).

"""REST endpoints. Vedi spec §8.3."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/runs")
    async def list_runs(request: Request, status: str | None = None,
                        limit: int = 100):
        db = request.app.state.db
        return db.list_runs(status=status, limit=limit)

    @router.get("/runs/{run_id}")
    async def get_run(request: Request, run_id: int, top_k: int = 10):
        db = request.app.state.db
        run = db.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run {run_id} non trovato")
        top = db.top_individuals(run_id, k=top_k)
        return {"run": run, "top": top}

    @router.get("/strategies")
    async def list_strategies():
        from backtest_suite.strategies import STRATEGY_REGISTRY
        out = []
        for sid, cls in STRATEGY_REGISTRY.items():
            out.append({
                "strategy_id":  sid,
                "display_name": cls.display_name,
                "timeframes":   list(cls.timeframes),
                "param_specs":  [{"name": p.name, "low": p.low, "high": p.high,
                                  "step": p.step, "is_int": p.is_int,
                                  "description": p.description}
                                 for p in cls.param_specs],
            })
        return out

    @router.get("/data/coverage")
    async def data_coverage(request: Request, symbol: str, timeframe: str):
        from backtest_suite import data_lake
        return data_lake.coverage(symbol, timeframe, root=request.app.state.data_root)

    @router.post("/data/fetch")
    async def data_fetch(request: Request, payload: dict):
        from datetime import datetime, timezone
        from backtest_suite import data_lake
        since = datetime.fromisoformat(payload["since"]).replace(tzinfo=timezone.utc)
        until = datetime.fromisoformat(payload["until"]).replace(tzinfo=timezone.utc)
        n = data_lake.fetch(payload["symbol"], payload["timeframe"], since, until,
                            force_refresh=payload.get("force_refresh", False),
                            root=request.app.state.data_root)
        return {"n_written": n}

    @router.post("/runs", status_code=202)
    async def create_run(request: Request, payload: dict):
        import asyncio

        from backtest_suite import data_lake
        from backtest_suite.config import RunConfig
        from backtest_suite.orchestrator import RunOrchestrator
        from backtest_suite.optimizer.types import GenerationEvent

        cfg = RunConfig.model_validate(payload)

        candles = getattr(request.app.state, "candles_override", None)
        if candles is None:
            candles = data_lake.load(cfg.symbol, cfg.timeframe,
                                     since=cfg.range.since, until=cfg.range.until,
                                     root=request.app.state.data_root)

        orch = RunOrchestrator(
            config=cfg, candles=candles,
            db_path=request.app.state.db.db_path,
            runs_dir=request.app.state.store.runs_dir,
        )
        db = request.app.state.db
        run_id = db.create_run(kind=cfg.kind, symbol=cfg.symbol,
                               timeframe=cfg.timeframe,
                               config_path=f"runs/{{run_id}}/manifest.yaml".format(run_id=0))

        registry = request.app.state.registry
        broker   = request.app.state.broker
        registry.register(run_id)

        loop = asyncio.get_running_loop()

        def _stop_flag() -> bool:
            return registry.is_stopped(run_id)

        def _publish_event(event_dict: dict) -> None:
            registry.push_event(run_id, event_dict)
            asyncio.run_coroutine_threadsafe(
                broker.publish(run_id, event_dict), loop,
            )

        def _bg_run():
            try:
                if cfg.kind == "ga":
                    out = _run_evolve_inline(orch, _stop_flag, _publish_event, run_id, db)
                elif cfg.kind == "grid":
                    out = _run_grid_inline(orch, _stop_flag, _publish_event, run_id, db)
                else:
                    raise ValueError(f"kind non supportato: {cfg.kind}")
                _publish_event({"type": "run_finished", "summary": out})
            except Exception as exc:
                _publish_event({"type": "run_failed", "error": str(exc)})
                db.update_run_status(run_id, status="failed", notes=str(exc))

        asyncio.create_task(asyncio.to_thread(_bg_run))
        return {"run_id": run_id, "status": "running"}

    @router.post("/runs/{run_id}/stop")
    async def stop_run(request: Request, run_id: int):
        registry = request.app.state.registry
        ok = registry.mark_stop(run_id)
        if not ok:
            raise HTTPException(status_code=404, detail="run non attivo")
        return {"ok": True}

    @router.post("/runs/{run_id}/individuals/{ind_id}/promote")
    async def promote(request: Request, run_id: int, ind_id: str,
                      payload: dict):
        import json
        import yaml
        from pathlib import Path

        db = request.app.state.db
        top = db.top_individuals(run_id, k=1000)
        target = next((r for r in top if r["individual_id"] == ind_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail="individuo non trovato")

        params = json.loads(target["params_json"])
        target_path = Path(payload.get("target_path", "state/strategy.yaml"))

        current = yaml.safe_load(target_path.read_text()) if target_path.exists() else {}
        diff: dict[str, dict] = {}

        # Strategy params: scrive le chiavi così come sono nel dict
        for k, v in params["strategy_params"].items():
            diff[k] = {"da": current.get(k), "a": v}
            current[k] = v
        # Risk params: convertiti da decimale a percentuale per coerenza yaml legacy
        for k, v in params["risk_params"].items():
            yaml_v = round(float(v) * 100.0, 4)
            diff[k] = {"da": current.get(k), "a": yaml_v}
            current[k] = yaml_v

        target_path.write_text(yaml.safe_dump(current, allow_unicode=True,
                                              sort_keys=False))
        return {"diff": diff, "wrote": str(target_path)}

    return router


def _run_evolve_inline(orch, stop_flag, publish_event, run_id, db) -> dict:
    """Versione di orch.evolve() che pubblica eventi sul broker."""
    import json
    from backtest_suite.optimizer.ga import evolve
    from backtest_suite.optimizer.fitness import score_individual
    from backtest_suite.optimizer.types import Scored

    execution = orch._build_exec()
    wf        = orch._build_wf_cfg()
    gcfg      = orch._build_ga_cfg()

    def _cb(event) -> None:
        publish_event({
            "type": "generation",
            "generation": event.generation,
            "best_fitness": event.best_fitness,
            "mean_fitness": event.mean_fitness,
            "species_counts": event.species_counts,
            "elapsed_sec": event.elapsed_sec,
        })

    result = evolve(gcfg, orch.candles, wf, execution,
                    stop_flag=stop_flag, progress_callback=_cb,
                    n_workers=orch.config.n_workers)

    for ev in result.history:
        detail = score_individual(ev.best_individual, orch.candles, wf, execution)
        db.insert_generation(run_id, generation=ev.generation,
                             scored=[Scored(individual=ev.best_individual,
                                            fitness=ev.best_fitness, detail=detail)])

    db.update_run_status(
        run_id, status=result.status,
        best_fitness=float(result.best_fitness),
        best_individual=json.dumps({
            "strategy_id":     result.best_individual.strategy_id,
            "strategy_params": result.best_individual.strategy_params,
            "risk_params":     result.best_individual.risk_params,
        }),
        n_generations=result.n_generations_completed,
    )
    return {"run_id": run_id, "status": result.status,
            "best_fitness": result.best_fitness}


def _run_grid_inline(orch, stop_flag, publish_event, run_id, db) -> dict:
    import json
    from backtest_suite.optimizer.grid import grid_search

    execution = orch._build_exec()
    wf        = orch._build_wf_cfg()
    gcfg      = orch._build_grid_cfg()

    def _cb(event) -> None:
        publish_event({
            "type": "grid_progress",
            "processed": event.processed, "total": event.total,
            "best_so_far": event.best_so_far, "elapsed_sec": event.elapsed_sec,
        })

    result = grid_search(gcfg, orch.candles, wf, execution,
                         stop_flag=stop_flag, progress_callback=_cb,
                         n_workers=orch.config.n_workers)
    db.insert_generation(run_id, generation=0, scored=result.all_scored)
    db.update_run_status(run_id, status=result.status,
                         best_fitness=float(result.best_fitness),
                         best_individual=json.dumps({
                             "strategy_id":     result.best_individual.strategy_id,
                             "strategy_params": result.best_individual.strategy_params,
                             "risk_params":     result.best_individual.risk_params,
                         }),
                         n_individuals=len(result.all_scored))
    return {"run_id": run_id, "status": result.status,
            "best_fitness": result.best_fitness}

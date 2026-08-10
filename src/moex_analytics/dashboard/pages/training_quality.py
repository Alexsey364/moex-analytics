"""Stage 31-34 data-quality research pages."""

import streamlit as st

from moex_analytics.dashboard.data_access import read_connection, table_exists


def render_corporate_actions() -> None:
    st.header("Корпоративные действия и качество цен")
    with read_connection() as con:
        if not table_exists(con, "corporate_action_candidate_episodes"):
            st.info("Stage 31 ещё не рассчитан.")
            return
        totals = con.execute(
            """SELECT count(*),count(*) FILTER(review_status='auto_validated'),
            count(*) FILTER(review_status='manual_review_required'),
            count(*) FILTER(review_status='unresolved')
            FROM corporate_action_candidate_episodes"""
        ).fetchone()
        cols = st.columns(4)
        for col, label, value in zip(
            cols, ("Episodes", "Подтверждено", "Manual review", "Unresolved"), totals, strict=True
        ):
            col.metric(label, value)
        st.dataframe(
            con.execute(
                """SELECT secid,priority,count(*) episodes,
                count(*) FILTER(review_status='auto_validated') resolved,
                count(*) FILTER(review_status='manual_review_required') manual_review,
                count(*) FILTER(review_status='unresolved') unresolved
                FROM corporate_action_candidate_episodes GROUP BY 1,2 ORDER BY 2,3 DESC"""
            ).df(),
            use_container_width=True,
        )
        st.caption("Raw EOD не изменяется. Ratio detection не является подтверждением события.")


def render_training_universe() -> None:
    st.header("Обучающая выборка")
    with read_connection() as con:
        if not table_exists(con, "training_universe_runs"):
            st.info("Stage 32 ещё не рассчитан.")
            return
        row = con.execute(
            """SELECT raw_securities,eligible_securities,rows,dates,dataset_version,cutoff
            FROM training_universe_runs ORDER BY created_at DESC LIMIT 1"""
        ).fetchone()
        cols = st.columns(4)
        cols[0].metric("Raw universe", row[0])
        cols[1].metric("Training universe", row[1])
        cols[2].metric("Rows", row[2])
        cols[3].metric("Dates", row[3])
        st.caption(f"Frozen dataset {row[4]}, cutoff {row[5]}")
        st.dataframe(
            con.execute(
                """SELECT quality_tier,count(DISTINCT secid) securities,count(*) rows
                FROM historical_training_panel WHERE dataset_version=? GROUP BY 1 ORDER BY 1""",
                [row[4]],
            ).df(),
            use_container_width=True,
        )


def render_clean_relearning() -> None:
    st.header("Повлияло ли качество данных на прогноз?")
    with read_connection() as con:
        if not table_exists(con, "clean_relearning_runs"):
            st.info("Stage 33 ещё не рассчитан.")
            return
        run = con.execute(
            """SELECT run_id,dataset_version,results,shadow_candidates,probability_approved,
            runtime_seconds FROM clean_relearning_runs ORDER BY started_at DESC LIMIT 1"""
        ).fetchone()
        cols = st.columns(4)
        cols[0].metric("Research results", run[2])
        cols[1].metric("New shadow candidates", run[3])
        cols[2].metric("Probability approved", run[4])
        cols[3].metric("Runtime, sec", round(run[5] or 0, 1))
        st.dataframe(
            con.execute(
                """SELECT experiment,secid,horizon,model,rows,balanced_accuracy,
                baseline_balanced_accuracy,improvement,ci_low,ci_high,status
                FROM clean_relearning_results WHERE run_id=? ORDER BY secid,horizon,experiment""",
                [run[0]],
            ).df(),
            use_container_width=True,
        )
        st.warning("Все результаты research/shadow. Production и probability gate не изменены.")


def render_quality_expansion() -> None:
    st.header("Кандидаты на повышение качества истории")
    with read_connection() as con:
        if not table_exists(con, "quality_expansion_runs"):
            st.info("Stage 34 ещё не рассчитан.")
            return
        run = con.execute(
            """SELECT run_id,tier_a_before,tier_b_before,tier_a_after,tier_b_after,
            candidates,requests,validated_resolutions,stop_reason
            FROM quality_expansion_runs ORDER BY started_at DESC LIMIT 1"""
        ).fetchone()
        cols = st.columns(4)
        cols[0].metric("Tier A", run[3], run[3] - run[1])
        cols[1].metric("Tier B", run[4], run[4] - run[2])
        cols[2].metric("Очередь", run[5])
        cols[3].metric("Official requests", run[6])
        st.caption(f"Stop condition: {run[8]}; validated resolutions: {run[7]}")
        st.dataframe(
            con.execute(
                """SELECT secid,current_tier,target_tier,blocking_issues_json,
                missing_evidence_json,queue_status FROM quality_promotion_queue
                WHERE run_id=? ORDER BY priority,secid""",
                [run[0]],
            ).df(),
            use_container_width=True,
        )
        st.warning("MOEX metadata подтверждает контекст бумаги, но не коэффициент корректировки цены.")


def render_issuer_context() -> None:
    st.header("PIT-фундаментал и отраслевой контекст")
    with read_connection() as con:
        if not table_exists(con, "issuer_context_runs"):
            st.info("Stage 35 ещё не рассчитан.")
            return
        run = con.execute(
            """SELECT run_id,status,fundamental_state_rows,derived_rows,sector_rows,
            issuers_five_periods FROM issuer_context_runs ORDER BY started_at DESC LIMIT 1"""
        ).fetchone()
        cols = st.columns(4)
        cols[0].metric("PIT states", run[2])
        cols[1].metric("Derived states", run[3])
        cols[2].metric("Sector observations", run[4])
        cols[3].metric("Issuers ≥5 periods", run[5])
        st.caption(f"Run {run[0]}, status: {run[1]}; X5 и FIVE не объединяются.")
        st.dataframe(
            con.execute(
                """SELECT issuer,validated_periods,coverage_status,limitation
                FROM stage30_fundamental_coverage ORDER BY issuer"""
            ).df(),
            use_container_width=True,
        )
        st.dataframe(
            con.execute(
                """SELECT issuer_group,sector_series,count(*) observations,min(trade_date) first_date,
                max(trade_date) last_date FROM issuer_sector_context_daily GROUP BY 1,2 ORDER BY 1"""
            ).df(),
            use_container_width=True,
        )


def render_issuer_evidence() -> None:
    st.header("Что действительно помогает прогнозировать каждую бумагу")
    with read_connection() as con:
        if not table_exists(con, "issuer_evidence_runs"):
            st.info("Stage 36 ещё не рассчитан.")
            return
        run = con.execute(
            """SELECT run_id,results,shadow_candidates,probability_approved,runtime_seconds
            FROM issuer_evidence_runs WHERE status='completed' ORDER BY started_at DESC LIMIT 1"""
        ).fetchone()
        cols = st.columns(4)
        cols[0].metric("Experiments", run[1])
        cols[1].metric("Shadow candidates", run[2])
        cols[2].metric("Probability approved", run[3])
        cols[3].metric("Runtime, sec", round(run[4] or 0, 1))
        st.dataframe(
            con.execute(
                """SELECT secid,horizon,experiment,rows,balanced_accuracy,improvement,
                ci_low,ci_high,fold_stability,status FROM issuer_evidence_results
                WHERE run_id=? ORDER BY secid,horizon,experiment""",
                [run[0]],
            ).df(),
            use_container_width=True,
        )
        st.warning("Research only: production unchanged, numerical probability gated.")


def render_fundamental_recovery() -> None:
    st.header("Восстановление официальных фундаментальных источников")
    with read_connection() as con:
        if not table_exists(con, "fundamental_recovery_runs"):
            st.info("Stage 37 ещё не рассчитан.")
            return
        run = con.execute(
            """SELECT run_id,sources_checked,sources_reachable,documents_discovered,
            validated_periods_before,validated_periods_after,manual_review_candidates,
            leakage_violations FROM fundamental_recovery_runs ORDER BY started_at DESC LIMIT 1"""
        ).fetchone()
        cols = st.columns(4)
        cols[0].metric("Sources reachable", run[2], f"из {run[1]}")
        cols[1].metric("Document links", run[3])
        cols[2].metric("Validated periods", run[5], run[5] - run[4])
        cols[3].metric("Manual review", run[6])
        st.dataframe(
            con.execute(
                """SELECT issuer,source_candidate,reachable,tls_status,content_type,
                machine_readable,status,blocker,discovered_links FROM source_resolution_registry
                WHERE run_id=? ORDER BY issuer,source_candidate""",
                [run[0]],
            ).df(),
            use_container_width=True,
        )
        st.caption(f"PIT leakage violations: {run[7]}. TLS validation was never disabled.")


def render_predictive_context() -> None:
    st.header("Sector, commodity и macro predictive context")
    with read_connection() as con:
        if not table_exists(con, "predictive_context_runs"):
            st.info("Stage 38 ещё не рассчитан.")
            return
        run = con.execute(
            """SELECT run_id,sector_rows,fx_rows,rates_rows,commodity_rows,
            synchronized_rows,exposure_rows,ablation_rows,runtime_seconds
            FROM predictive_context_runs ORDER BY started_at DESC LIMIT 1"""
        ).fetchone()
        cols = st.columns(4)
        cols[0].metric("Synchronized", run[5])
        cols[1].metric("Factor exposures", run[6])
        cols[2].metric("Ablations", run[7])
        cols[3].metric("Runtime, sec", round(run[8] or 0, 1))
        st.dataframe(
            con.execute(
                """SELECT dataset_family,series_id,rows,earliest,latest,pit_status,
                quality_status,limitation FROM predictive_context_coverage
                WHERE run_id=? ORDER BY dataset_family,series_id""",
                [run[0]],
            ).df(),
            use_container_width=True,
        )
        st.dataframe(
            con.execute(
                """SELECT secid,horizon,experiment,rows,balanced_accuracy,improvement,
                ci_low,ci_high,status FROM predictive_context_ablation
                WHERE run_id=? ORDER BY secid,horizon,experiment""",
                [run[0]],
            ).df(),
            use_container_width=True,
        )
        st.warning("Urals и fertilizer prices не подменены: requires_paid_data.")

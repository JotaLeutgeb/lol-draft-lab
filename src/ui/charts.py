import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import numpy as np

def build_gold_timeline_chart(df_gd, match_results):
    if df_gd.empty or match_results.empty:
        fig = go.Figure()
        fig.update_layout(template="plotly_dark", paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
        fig.add_annotation(text="Datos insuficientes para la línea de tiempo", showarrow=False, font=dict(color="#94A3B8"))
        return fig
    
    df = df_gd.merge(match_results[["match_id", "result"]], on="match_id")
    
    # Agrupar por resultado y minuto
    # Solo nos interesan las columnas gold_diff_minX
    id_vars = ["match_id", "result"]
    if "role" in df.columns: id_vars.append("role")
    if "participant_id" in df.columns: id_vars.append("participant_id")
    
    melted = df.melt(id_vars=id_vars, var_name="minute_col", value_name="gold_diff")
    
    # Extraer el número del minuto y convertir a int, eliminando nans (columnas que no eran de gold_diff)
    melted["minute"] = melted["minute_col"].str.extract(r'(\d+)')[0]
    melted = melted.dropna(subset=["minute"])
    melted["minute"] = melted["minute"].astype(int)
    
    avg_gd = melted.groupby(["result", "minute"])["gold_diff"].mean().reset_index()
    avg_gd["Resultado"] = avg_gd["result"].map({True: "Victoria", False: "Derrota"})
    
    fig = px.line(
        avg_gd, x="minute", y="gold_diff", color="Resultado",
        labels={"minute": "Minuto", "gold_diff": "Gold Diff Equipo"},
        color_discrete_map={"Victoria": "#4ADE80", "Derrota": "#F87171"},
        template="plotly_dark"
    )
    fig.add_hline(y=0, line_dash="dash", line_color="#475569")
    fig.update_layout(
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        font_family="Outfit", margin=dict(l=20, r=20, t=20, b=20)
    )
    return fig

def build_radar_chart(metrics, percentiles, name, role):
    from src.visualization import plot_ghost_radar
    return plot_ghost_radar(metrics, percentiles, name, role, "Challenger")

def build_synergy_heatmap_chart(df_summary, df_bench=None):
    """Recibe df_summary (output de compute_player_impact_summary) directamente."""
    from src.analysis import compute_synergy_matrix_display
    matrix_df = compute_synergy_matrix_display(df_summary)
    
    roles = ["TOP", "JUNGLE", "MID", "BOT", "SUPPORT"]
    team_data = matrix_df.reindex(index=roles, columns=roles).apply(pd.to_numeric, errors='coerce').fillna(0).values
    
    # 2. Obtener la Matriz Benchmark (Promedios de Supabase)
    benchmark_matrix = np.zeros((5, 5))
    if df_bench is not None and not df_bench.empty:
        # Mapeo de columnas de sinergia en la DB a pares de la matriz
        pair_map = {
            ("JUNGLE", "SUPPORT"): "synergy_jg_sup", ("JUNGLE", "MID"): "synergy_jg_mid",
            ("JUNGLE", "TOP"):     "synergy_jg_top", ("JUNGLE", "BOT"): "synergy_jg_adc",
            ("BOT",    "SUPPORT"): "synergy_adc_sup", ("MID", "BOT"):   "synergy_mid_bot",
            ("MID",    "TOP"):     "synergy_mid_top", ("MID", "SUPPORT"): "synergy_mid_sup",
            ("TOP",    "BOT"):     "synergy_top_bot", ("TOP", "SUPPORT"): "synergy_top_sup"
        }
        
        # Calcular promedios globales de la DB para cada par
        for (r1, r2), col in pair_map.items():
            if col in df_bench.columns:
                p50 = df_bench[col].mean()
                idx1, idx2 = roles.index(r1), roles.index(r2)
                benchmark_matrix[idx1, idx2] = p50
                benchmark_matrix[idx2, idx1] = p50

    # 3. Calcular el GAP y preparar Hover Data
    plot_data = np.zeros((5, 5))   # Los valores para el color (GAP)
    display_data = np.zeros((5, 5)) # Los valores para el texto (Team Value)
    custom_data = np.zeros((5, 5, 2)) # [Benchmark Avg, Gap] para el tooltip
    
    for i in range(5):
        for j in range(5):
            val = team_data[i, j]
            b_val = benchmark_matrix[i, j]
            gap = val - b_val
            
            if i == j:
                plot_data[i, j] = 0
                display_data[i, j] = 0
                custom_data[i, j] = [0, 0]
            else:
                plot_data[i, j] = gap
                display_data[i, j] = val
                custom_data[i, j] = [b_val, gap]

    # 4. Determinar rango simétrico
    max_gap = max(abs(np.nanmin(plot_data)), abs(np.nanmax(plot_data)), 0.1)

    fig = px.imshow(
        plot_data, x=roles, y=roles,
        color_continuous_scale="RdYlGn", 
        aspect="auto",
        template="plotly_dark",
        zmin=-max_gap, zmax=max_gap
    )
    
    # Añadir texto (Solo nuestro valor) y Custom Hover
    fig.update_traces(
        text=display_data, 
        texttemplate="%{text:.2f}",
        customdata=custom_data,
        hovertemplate="<b>Sinergia %{y}-%{x}</b><br>" +
                      "Nuestro Valor: %{text:.2f}<br>" +
                      "Avg Challenger: %{customdata[0]:.2f}<br>" +
                      "<b>Gap Táctico: %{customdata[1]:+.2f}</b><extra></extra>"
    )
    
    fig.update_layout(
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        font_family="Outfit", margin=dict(l=20, r=20, t=40, b=20),
        title=dict(text="Synergy GAP vs Challenger Elite", font=dict(size=14, color="#4ADE80")),
        coloraxis_colorbar=dict(title="Gap vs P50")
    )
    return fig

def build_density_heatmap(matrix, title, color_scale="Reds"):
    if matrix.empty:
        fig = go.Figure()
        fig.add_annotation(text="Sin datos de eventos", showarrow=False)
        fig.update_layout(template="plotly_dark", paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
        return fig
        
    fig = px.imshow(
        matrix.values,
        x=matrix.columns,
        y=matrix.index,
        color_continuous_scale=color_scale,
        text_auto=True,
        aspect="auto",
        height=400,
        labels=dict(x="Minuto", y="Rol", color="Cantidad"),
        template="plotly_dark"
    )
    fig.update_layout(
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        font_family="Outfit", margin=dict(l=20, r=20, t=40, b=20),
        title=dict(text=title, font=dict(size=14, color="#94A3B8")),
        xaxis=dict(
            title="Minuto"
        )
    )
    return fig

def build_phase_winrate_chart(phase_stats, match_results):
    phases = list(phase_stats.keys())
    winrates = [s["win_rate"] * 100 for s in phase_stats.values()]
    
    fig = go.Figure(data=[
        go.Bar(
            x=phases, y=winrates,
            marker_color=['#4ADE80' if w >= 50 else '#F87171' for w in winrates],
            text=[f"{w:.0f}%" for w in winrates],
            textposition='auto',
        )
    ])
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        font_family="Outfit", margin=dict(l=20, r=20, t=20, b=20),
        yaxis=dict(range=[0, 100], title="Winrate %"),
        xaxis=dict(title="Fase del Juego")
    )
    return fig

def build_objectives_chart(obj_perf, df_obj_team):
    """
    Muestra el Objective Control Score del equipo en Victorias vs Derrotas.
    Usa avg_obj_score_win/loss (sistema individual, igual que benchmarks Challenger).
    """
    our_win   = obj_perf.get("avg_obj_score_win", 0)
    our_loss  = obj_perf.get("avg_obj_score_loss", 0)
    rival_win = obj_perf.get("avg_rival_score_win", 0)
    rival_loss= obj_perf.get("avg_rival_score_loss", 0)
    obj_conv  = obj_perf.get("objective_conversion", 0)

    categories = ["Score Obj (Victoria)", "Score Obj (Derrota)"]

    fig = go.Figure(data=[
        go.Bar(
            name="Nuestro Equipo",
            x=categories,
            y=[our_win, our_loss],
            marker_color=["#4ADE80", "#F87171"],
            text=[f"{our_win:.1f}", f"{our_loss:.1f}"],
            textposition="auto",
        ),
        go.Bar(
            name="Rival",
            x=categories,
            y=[rival_win, rival_loss],
            marker_color=["rgba(74,222,128,0.3)", "rgba(248,113,113,0.3)"],
            text=[f"{rival_win:.1f}", f"{rival_loss:.1f}"],
            textposition="auto",
        ),
    ])
    fig.add_annotation(
        x=0.5, y=1.05, xref="paper", yref="paper",
        text=f"Conversión de objetivos: <b>{obj_conv*100:.1f}%</b>",
        showarrow=False, font=dict(color="#94A3B8", size=12)
    )
    fig.update_layout(
        barmode="group", template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_family="Outfit", margin=dict(l=20, r=20, t=40, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        yaxis=dict(title="Objective Control Score"),
    )
    return fig

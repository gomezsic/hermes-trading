// charts.js — wrapper Chart.js per fitness e equity.
window.charts = (function () {
  let fitnessChartInstance = null;

  function fitnessChart(canvasId) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return null;
    if (fitnessChartInstance) fitnessChartInstance.destroy();
    fitnessChartInstance = new Chart(ctx, {
      type: 'line',
      data: { labels: [], datasets: [
        { label: 'best', data: [], borderColor: '#7dffaf', borderWidth: 2, fill: false, pointRadius: 1 },
        { label: 'mean', data: [], borderColor: '#888', borderWidth: 1.5, fill: false, pointRadius: 1, borderDash: [3, 3] },
      ]},
      options: {
        responsive: true,
        scales: { x: { ticks: { color: '#8b949e' } }, y: { ticks: { color: '#8b949e' } } },
        plugins: { legend: { labels: { color: '#d1d5db' } } },
      },
    });
    return fitnessChartInstance;
  }

  function pushFitnessPoint(generation, best, mean) {
    if (!fitnessChartInstance) return;
    fitnessChartInstance.data.labels.push(generation);
    fitnessChartInstance.data.datasets[0].data.push(best);
    fitnessChartInstance.data.datasets[1].data.push(mean);
    fitnessChartInstance.update('none');
  }

  return { fitnessChart, pushFitnessPoint };
})();

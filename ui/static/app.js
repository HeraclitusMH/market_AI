/* Market AI Dashboard JS */

async function postControl(url) {
  try {
    const resp = await fetch(url, { method: 'POST' });
    const data = await resp.json();
    console.log(url, data);
    location.reload();
  } catch (e) {
    alert('Error: ' + e.message);
  }
}

function buildEquityChart(canvasId, labels, equity, drawdown) {
  const ctx = document.getElementById(canvasId).getContext('2d');
  new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels.map(l => new Date(l).toLocaleDateString()),
      datasets: [
        {
          label: 'Net Liquidation ($)',
          data: equity,
          borderColor: '#4f8cff',
          backgroundColor: 'rgba(79,140,255,0.08)',
          fill: true,
          tension: 0.3,
          yAxisID: 'y',
        },
        {
          label: 'Drawdown (%)',
          data: drawdown,
          borderColor: '#e74c3c',
          backgroundColor: 'rgba(231,76,60,0.08)',
          fill: true,
          tension: 0.3,
          yAxisID: 'y1',
        }
      ]
    },
    options: {
      responsive: true,
      interaction: { mode: 'index', intersect: false },
      scales: {
        y: { position: 'left', title: { display: true, text: 'Equity ($)' } },
        y1: { position: 'right', reverse: true, title: { display: true, text: 'Drawdown %' }, grid: { drawOnChartArea: false } },
      }
    }
  });
}

function buildSentimentChart(canvasId, labels, scores) {
  const ctx = document.getElementById(canvasId).getContext('2d');
  new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels.map(l => new Date(l).toLocaleDateString()),
      datasets: [{
        label: 'Market Sentiment',
        data: scores,
        borderColor: '#f39c12',
        backgroundColor: 'rgba(243,156,18,0.1)',
        fill: true,
        tension: 0.3,
      }]
    },
    options: {
      responsive: true,
      scales: {
        y: { min: -1, max: 1, title: { display: true, text: 'Score' } }
      }
    }
  });
}

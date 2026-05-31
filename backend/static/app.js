const API_BASE_URL = '';

let ipTimelineChart = null;
let ruleTimelineChart = null;

function showView(viewId) {
    document.querySelectorAll('.view').forEach(view => view.classList.add('hidden'));
    document.getElementById(`${viewId}-view`).classList.remove('hidden');
    
    if (viewId === 'upload') {
        loadMissingData();
    }
}

let missingData = [];

async function loadMissingData() {
    try {
        const response = await fetch(`${API_BASE_URL}/summaries/missing`);
        const data = await response.json();
        missingData = data.days;
        renderDataManagementTable();
    } catch (error) {
        console.error('Error loading missing data:', error);
    }
}

function renderDataManagementTable() {
    const tbody = document.getElementById('missing-data-table-body');
    const filterIncomplete = document.getElementById('filter-incomplete').checked;
    if (!tbody) return;
    tbody.innerHTML = '';
    
    missingData.forEach((day, index) => {
        const isPresent = day.status === 'present';
        if (filterIncomplete && isPresent) return;

        const row = document.createElement('tr');
        row.className = isPresent ? 'bg-green-50' : (day.is_locked ? 'bg-gray-50 opacity-50 cursor-not-allowed' : 'bg-red-50 cursor-pointer');
        
        if (!isPresent && !day.is_locked) {
            row.onclick = () => toggleRow(index);
        }
        
        const uploadedAt = day.uploaded_at ? new Date(day.uploaded_at).toLocaleString() : '-';
        
        row.innerHTML = `
            <td class="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">${day.date} ${day.is_locked ? '(Today - Locked)' : ''}</td>
            <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                <span class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full ${isPresent ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'}">
                    ${day.status}
                </span>
            </td>
            <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500">${day.count}</td>
            <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500">${uploadedAt}</td>
            <td class="px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
                ${isPresent ? `<button onclick="event.stopPropagation(); clearData('${day.date}')" class="text-red-600 hover:text-red-900 mr-4">Clear</button>` : ''}
                ${!isPresent && !day.is_locked ? '<span class="text-blue-600 hover:text-blue-900">Unfold</span>' : ''}
            </td>
        `;
        tbody.appendChild(row);

        if (!isPresent && !day.is_locked) {
            const detailRow = document.createElement('tr');
            detailRow.id = `detail-${index}`;
            detailRow.className = 'hidden bg-white';
            detailRow.innerHTML = `
                <td colspan="5" class="px-6 py-4 border-b">
                    <div class="space-y-4" onclick="event.stopPropagation()">
                        <div>
                            <p class="text-xs font-bold text-gray-500 uppercase mb-1">Splunk Query</p>
                            <code class="block bg-gray-100 p-2 text-xs rounded border break-all">${day.splunk_query}</code>
                        </div>
                        <div class="flex items-center space-x-4">
                            <a href="${day.splunk_link}" target="_blank" class="bg-blue-600 text-white px-4 py-2 rounded text-sm hover:bg-blue-700">Open in Splunk</a>
                        </div>
                    </div>
                </td>
            `;
            tbody.appendChild(detailRow);
        }
    });
}

function renderTable() {
    renderDataManagementTable();
}

async function clearData(date) {
    if (!confirm(`Are you sure you want to clear all data for ${date}?`)) return;
    
    try {
        const response = await fetch(`${API_BASE_URL}/summaries/date/${date}`, {
            method: 'DELETE'
        });
        if (response.ok) {
            loadMissingData();
        } else {
            alert('Failed to clear data');
        }
    } catch (error) {
        console.error('Error clearing data:', error);
    }
}

function toggleRow(index) {
    const detail = document.getElementById(`detail-${index}`);
    detail.classList.toggle('hidden');
}

function handleDropGlobal(event) {
    event.preventDefault();
    event.currentTarget.classList.remove('border-blue-400');
    const file = event.dataTransfer.files[0];
    if (file) handleFileGlobal(file);
}

function handleFileGlobal(file) {
    const reader = new FileReader();
    reader.onload = async (e) => {
        const text = e.target.result;
        await processAndUploadGlobal(text);
    };
    reader.readAsText(file);
}

async function processAndUploadGlobal(csvText) {
    const statusDiv = document.getElementById('status-global');
    statusDiv.className = 'mt-2 text-sm text-blue-600';
    statusDiv.innerText = 'Processing...';
    statusDiv.classList.remove('hidden');

    try {
        const jsonData = parseCSV(csvText);
        if (jsonData.length === 0) throw new Error('No data found in CSV');

        const response = await fetch(`${API_BASE_URL}/summaries/upload`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ data: jsonData })
        });
        
        if (response.ok) {
            statusDiv.className = 'mt-2 text-sm text-green-600 font-bold';
            statusDiv.innerText = `Successfully uploaded ${jsonData.length} records! Refreshing...`;
            setTimeout(loadMissingData, 1500);
        } else {
            const err = await response.json();
            statusDiv.className = 'mt-2 text-sm text-red-600';
            statusDiv.innerText = 'Upload failed: ' + (err.message || 'Unknown error');
        }
    } catch (err) {
        statusDiv.className = 'mt-2 text-sm text-red-600';
        statusDiv.innerText = 'Error: ' + err.message;
    }
}

function parseCSV(csvText, defaultDate = null) {
    const lines = csvText.split(/\r?\n/);
    if (lines.length < 2) return [];
    
    const headers = lines[0].split(',').map(h => h.trim().replace(/^"|"$/g, ''));
    const jsonData = [];
    
    for (let i = 1; i < lines.length; i++) {
        if (!lines[i].trim()) continue;
        const values = lines[i].split(',').map(v => v.trim().replace(/^"|"$/g, ''));
        const obj = {};
        if (defaultDate) obj.date = defaultDate;

        headers.forEach((header, index) => {
            obj[header] = values[index];
        });

        jsonData.push(obj);
    }
    return jsonData;
}

function handleDrop(event, date) {
    // Deprecated in favor of global upload
    event.preventDefault();
}

function handleFile(file, date) {
    // Deprecated in favor of global upload
}

async function processAndUpload(csvText, date) {
    // Deprecated in favor of global upload
}


async function searchIP() {
    const ip = document.getElementById('ip-input').value;
    if (!ip) return;

    const header = document.getElementById('search-ip-header');
    
    try {
        const response = await fetch(`${API_BASE_URL}/search/ip/${ip}`);
        const data = await response.json();
        
        let headerText = `Showing results for: ${ip}`;
        const warning = data.warning;
        if (warning) {
            headerText += ` <span class="text-red-600 ml-2">(Warning: ${warning})</span>`;
        }
        header.innerHTML = headerText;
        header.classList.remove('hidden');

        renderTimeline('ip-timeline-chart', data.timeline, ipTimelineChart, (chart) => ipTimelineChart = chart);
        renderResultsTable('ip-src-hits', ['Rule', 'Count', 'Last Activity'], data.src_hits, (item) => [item.rule, item.count, item.last_activity]);
        renderResultsTable('ip-dst-hits', ['Rule', 'Count', 'Last Activity'], data.dst_hits, (item) => [item.rule, item.count, item.last_activity]);
        
    } catch (error) {
        console.error('Error searching IP:', error);
    }
}

async function searchRule() {
    const rule = document.getElementById('rule-input').value;
    if (!rule) return;

    const header = document.getElementById('search-rule-header');
    
    try {
        const response = await fetch(`${API_BASE_URL}/search/rule/${rule}`);
        const data = await response.json();
        
        let headerText = `Showing results for: ${rule}`;
        const warning = data.warning;
        if (warning) {
            headerText += ` <span class="text-red-600 ml-2">(Warning: ${warning})</span>`;
        }
        header.innerHTML = headerText;
        header.classList.remove('hidden');

        renderTimeline('rule-timeline-chart', data.timeline, ruleTimelineChart, (chart) => ruleTimelineChart = chart);
        renderResultsTable('rule-src-ips', ['IP', 'Count', 'Last Activity'], data.active_sources, (item) => [item.ip, item.count, item.last_activity]);
        renderResultsTable('rule-dst-ips', ['IP', 'Count', 'Last Activity'], data.active_destinations, (item) => [item.ip, item.count, item.last_activity]);
        
    } catch (error) {
        console.error('Error searching rule:', error);
    }
}

function renderTimeline(canvasId, timelineData, chartInstance, setChartInstance) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    
    if (chartInstance) {
        chartInstance.destroy();
    }
    
    const newChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: timelineData.map(d => d.timestamp),
            datasets: [{
                label: 'Event Count',
                data: timelineData.map(d => d.count),
                borderColor: 'rgb(59, 130, 246)',
                tension: 0.1
            }]
        },
        options: {
            scales: {
                y: { beginAtZero: true }
            }
        }
    });
    
    setChartInstance(newChart);
}

function renderResultsTable(containerId, headers, data, rowMapper) {
    const container = document.getElementById(containerId);
    if (!container) return;
    if (!data || data.length === 0) {
        container.innerHTML = '<p class="text-gray-500">No activity found.</p>';
        return;
    }
    
    let html = `<table class="min-w-full divide-y divide-gray-200">
        <thead class="bg-gray-50">
            <tr>
                ${headers.map(h => `<th class="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">${h}</th>`).join('')}
            </tr>
        </thead>
        <tbody class="bg-white divide-y divide-gray-200">
            ${data.map(item => {
                const cells = rowMapper(item);
                return `<tr>${cells.map(c => `<td class="px-4 py-2 whitespace-nowrap text-sm text-gray-900">${c}</td>`).join('')}</tr>`;
            }).join('')}
        </tbody>
    </table>`;
    
    container.innerHTML = html;
}

// Initial load
loadMissingData();

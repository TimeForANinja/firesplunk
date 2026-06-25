/**
 * FireSplunk Frontend Application
 * Handles data management, search (IP/Rule), and activity visualization.
 */

// =============================================================================
// STATE & CONFIGURATION
// =============================================================================

const API_BASE_URL = '';
let ipTimelineChart = null;
let ruleTimelineChart = null;
let missingData = [];
let indexState = null;
let indexStateTimer = null;
let activeTasks = [];

// Search state
let isSearching = false;
const tableSortStates = {
    'missing-data': { colIndex: 0, direction: -1 }
};

// =============================================================================
// NAVIGATION & VIEW MANAGEMENT
// =============================================================================

/**
 * Switches between different sections of the application.
 */
function showView(viewId) {
    document.querySelectorAll('.view').forEach(view => view.classList.add('hidden'));
    document.getElementById(`${viewId}-view`).classList.remove('hidden');
    
    if (viewId === 'upload') {
        loadMissingData();
        startIndexStatePolling();
    } else {
        stopIndexStatePolling();
    }
}

/**
 * Periodically fetches index state and active tasks.
 */
function startIndexStatePolling() {
    if (indexStateTimer) return;
    loadActiveTasks();
    indexStateTimer = setInterval(() => {
        loadActiveTasks();
    }, 5000); // Poll every 5 seconds
}

function stopIndexStatePolling() {
    if (indexStateTimer) {
        clearInterval(indexStateTimer);
        indexStateTimer = null;
    }
}

/**
 * Fetches active tasks.
 */
async function loadActiveTasks() {
    try {
        const response = await fetch(`${API_BASE_URL}/tasks`);
        const data = await response.json();
        activeTasks = data.tasks || [];
        renderTaskList();
    } catch (error) {
        console.error('Error loading active tasks:', error);
    }
}

/**
 * Manually triggers an index rebuild.
 */
async function requestRebuild() {
    try {
        const response = await fetch(`${API_BASE_URL}/index/rebuild`, {
            method: 'POST'
        });
        if (response.ok) {
            loadActiveTasks();
        } else {
            alert('Failed to request rebuild');
        }
    } catch (error) {
        console.error('Error requesting rebuild:', error);
        alert('Error requesting rebuild');
    }
}

/**
 * Renders the active tasks in the UI.
 */
function renderTaskList() {
    const container = document.getElementById('task-list-container');
    const list = document.getElementById('task-list');
    if (!container || !list) return;

    if (activeTasks.length === 0) {
        container.classList.add('hidden');
        return;
    }

    container.classList.remove('hidden');
    list.innerHTML = '';

    activeTasks.forEach(task => {
        const div = document.createElement('div');
        div.className = 'bg-white border rounded p-3 flex flex-col gap-1 shadow-sm';
        
        const header = document.createElement('div');
        header.className = 'flex justify-between items-center';
        
        const type = document.createElement('span');
        type.className = 'font-bold text-sm uppercase text-gray-700';
        type.innerText = task.type.replace('_', ' ');
        
        const state = document.createElement('span');
        let stateClass = 'bg-gray-100 text-gray-800';
        if (task.state === 'work-in-progress') {
            stateClass = 'bg-blue-100 text-blue-800';
        } else if (task.state === 'done') {
            stateClass = 'bg-green-100 text-green-800';
        } else if (task.state === 'failed') {
            stateClass = 'bg-red-100 text-red-800';
        } else if (task.state === 'stale') {
            stateClass = 'bg-red-100 text-red-800';
        }
        state.className = `text-xs px-2 py-0.5 rounded-full font-medium ${stateClass}`;
        state.innerText = task.state;
        
        header.appendChild(type);
        header.appendChild(state);
        
        const progressContainer = document.createElement('div');
        progressContainer.className = 'w-full bg-gray-200 rounded-full h-2 mt-1';
        
        const progressBar = document.createElement('div');
        let barClass = 'bg-blue-600';
        let progress = task.progress;

        if (task.state === 'done') barClass = 'bg-green-600';
        if (task.state === 'failed') barClass = 'bg-red-600';
        if (task.state === 'stale') {
            barClass = 'bg-red-600';
            progress = 100;
        }
        progressBar.className = `${barClass} h-2 rounded-full transition-all duration-500`;
        progressBar.style.width = `${progress}%`;
        
        progressContainer.appendChild(progressBar);
        
        const info = document.createElement('div');
        info.className = 'text-xs text-gray-500 flex justify-between';
        
        const leftInfo = document.createElement('span');
        leftInfo.innerText = task.additional_info;
        
        const rightInfo = document.createElement('span');
        rightInfo.innerText = `${progress}%`;
        
        info.appendChild(leftInfo);
        info.appendChild(rightInfo);
        
        div.appendChild(header);
        div.appendChild(progressContainer);
        div.appendChild(info);

        if (task.state === 'failed' || task.state === 'stale') {
            const retryBtn = document.createElement('button');
            retryBtn.className = 'mt-2 text-xs bg-gray-100 hover:bg-gray-200 text-gray-700 py-1 px-2 rounded border border-gray-300 transition-colors';
            retryBtn.innerText = 'Retry Task';
            retryBtn.onclick = async () => {
                try {
                    const response = await fetch(`${API_BASE_URL}/tasks/${task.id}/retry`, { method: 'POST' });
                    if (response.ok) {
                        loadActiveTasks();
                    } else {
                        alert('Failed to retry task');
                    }
                } catch (error) {
                    console.error('Error retrying task:', error);
                }
            };
            div.appendChild(retryBtn);
        }
        
        list.appendChild(div);
    });
}


// =============================================================================
// DATA MANAGEMENT (UPLOAD & OVERVIEW)
// =============================================================================

/**
 * Fetches the status of data for the last N days.
 */
async function loadMissingData() {
    try {
        const response = await fetch(`${API_BASE_URL}/summaries/status`);
        const data = await response.json();
        missingData = data.days;
        renderDataManagementTable();
    } catch (error) {
        console.error('Error loading missing data:', error);
    }
}

/**
 * Renders the Data Management table showing data presence/absence.
 */
function renderDataManagementTable() {
    const tbody = document.getElementById('missing-data-table-body');
    const filterIncomplete = document.getElementById('filter-incomplete').checked;
    if (!tbody) return;
    tbody.innerHTML = '';
    
    let displayData = [...missingData];
    const sortState = tableSortStates['missing-data'];
    
    displayData.sort((a, b) => {
        let valA, valB;
        switch(sortState.colIndex) {
            case 0: valA = a.date; valB = b.date; break;
            case 1: valA = a.status; valB = b.status; break;
            case 2: valA = a.count; valB = b.count; break;
            case 3: valA = a.uploaded_at || ''; valB = b.uploaded_at || ''; break;
        }
        if (typeof valA === 'number') return (valA - valB) * sortState.direction;
        return naturalCompare(valA, valB) * sortState.direction;
    });

    displayData.forEach((day, index) => {
        const isPresent = day.status === 'present';
        const isLocked = day.status === 'locked';

        if (filterIncomplete && isPresent) return;

        const row = document.createElement('tr');
        
        let rowClass = 'bg-white';
        if (isPresent) rowClass = 'bg-green-50';
        else if (isLocked) rowClass = 'bg-gray-50 opacity-50 cursor-not-allowed';
        else rowClass = 'bg-red-50 cursor-pointer';
        
        row.className = rowClass;
        
        if (!isPresent && !isLocked) {
            row.onclick = () => toggleRow(index);
        }
        
        const uploadedAt = day.uploaded_at ? new Date(day.uploaded_at).toLocaleString() : '-';
        
        let statusBadgeClass = 'bg-gray-100 text-gray-800';
        if (isPresent) statusBadgeClass = 'bg-green-100 text-green-800';
        else if (isLocked) statusBadgeClass = 'bg-gray-100 text-gray-800';
        else statusBadgeClass = 'bg-red-100 text-red-800';

        row.innerHTML = `
            <td class="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">${day.date} ${isLocked ? '(Today - Locked)' : ''}</td>
            <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                <span class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full ${statusBadgeClass}">
                    ${day.status}
                </span>
            </td>
            <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500">${day.count}</td>
            <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500">${uploadedAt}</td>
            <td class="px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
                ${isPresent ? `<button onclick="event.stopPropagation(); clearData('${day.date}')" class="text-red-600 hover:text-red-900 mr-4">Clear</button>` : ''}
                ${(!isPresent && !isLocked) ? '<span class="text-blue-600 hover:text-blue-900">Unfold</span>' : ''}
            </td>
        `;
        tbody.appendChild(row);

        if (!isPresent && !isLocked) {
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

/** Triggered by filter checkbox */
function renderTable() {
    renderDataManagementTable();
}

/** Toggles detail view for missing data rows */
function toggleRow(index) {
    const detail = document.getElementById(`detail-${index}`);
    if (detail) {
        detail.classList.toggle('hidden');
    }
}

/** Sorts Missing Data table */
function sortMissingData(colIndex) {
    const sortState = tableSortStates['missing-data'];
    if (sortState.colIndex === colIndex) {
        sortState.direction *= -1;
    } else {
        sortState.colIndex = colIndex;
        sortState.direction = 1;
    }
    renderDataManagementTable();
}

/** Deletes data for a specific date */
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

// =============================================================================
// CSV PROCESSING & UPLOAD
// =============================================================================

function handleDropGlobal(event) {
    event.preventDefault();
    event.currentTarget.classList.remove('border-blue-400');
    const file = event.dataTransfer.files[0];
    if (file) handleFileGlobal(file);
}

function handleFileGlobal(file) {
    processAndUploadGlobal(file);
}

/**
 * Sends CSV file to the backend.
 */
async function processAndUploadGlobal(file) {
    const statusDiv = document.getElementById('status-global');
    statusDiv.className = 'mt-2 text-sm text-blue-600';
    statusDiv.innerText = 'Uploading...';
    statusDiv.classList.remove('hidden');

    try {
        const formData = new FormData();
        formData.append('file', file);

        const response = await fetch(`${API_BASE_URL}/summaries/upload`, {
            method: 'POST',
            body: formData
        });
        
        const result = await response.json();
        if (response.ok) {
            statusDiv.className = 'mt-2 text-sm text-green-600 font-bold';
            statusDiv.innerText = result.message + ' Refreshing...';
            setTimeout(loadMissingData, 1500);
        } else {
            statusDiv.className = 'mt-2 text-sm text-red-600';
            statusDiv.innerText = 'Upload failed: ' + (result.message || 'Unknown error');
        }
    } catch (err) {
        statusDiv.className = 'mt-2 text-sm text-red-600';
        statusDiv.innerText = 'Error: ' + err.message;
    }
}

// =============================================================================
// SEARCH FUNCTIONALITY
// =============================================================================

async function searchIP() {
    if (isSearching) return;
    const ip = document.getElementById('ip-input').value;
    if (!ip) return;

    const header = document.getElementById('search-ip-header');
    header.innerHTML = '<span class="animate-pulse">Search in progress...</span>';
    header.classList.remove('hidden');
    
    setSearching(true);
    
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
    } finally {
        setSearching(false);
    }
}

async function searchRule() {
    if (isSearching) return;
    const rule = document.getElementById('rule-input').value;
    if (!rule) return;

    const header = document.getElementById('search-rule-header');
    header.innerHTML = '<span class="animate-pulse">Search in progress...</span>';
    header.classList.remove('hidden');

    setSearching(true);
    
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
        renderResultsTable('rule-ports', ['Port', 'Count', 'Last Activity'], data.ports, (item) => [item.port, item.count, item.last_activity]);
        
    } catch (error) {
        console.error('Error searching rule:', error);
    } finally {
        setSearching(false);
    }
}

// =============================================================================
// UI HELPERS & VISUALIZATION
// =============================================================================

/**
 * Creates/Updates a Chart.js timeline chart.
 */
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
                backgroundColor: 'rgba(59, 130, 246, 0.5)',
                pointBackgroundColor: timelineData.map(d => {
                    if (!d.has_data) return 'rgb(239, 68, 68)'; // Red: no data for the day
                    if (d.count === 0) return 'rgb(34, 197, 94)'; // Green: we have data, but it's 0
                    return 'rgb(59, 130, 246)'; // Blue: regular datapoint
                }),
                pointBorderColor: timelineData.map(d => {
                    if (!d.has_data) return 'rgb(239, 68, 68)';
                    if (d.count === 0) return 'rgb(34, 197, 94)';
                    return 'rgb(59, 130, 246)';
                }),
                pointRadius: 5,
                pointHoverRadius: 7,
                tension: 0.1
            }]
        },
        options: {
            plugins: {
                legend: { display: false }
            },
            scales: {
                y: { beginAtZero: true }
            }
        }
    });
    
    setChartInstance(newChart);
}

/**
 * Sets the searching state and updates UI.
 */
function setSearching(state) {
    isSearching = state;
    if (state) {
        document.querySelectorAll('button').forEach(btn => btn.disabled = true);
    } else {
        document.querySelectorAll('button').forEach(btn => btn.disabled = false);
    }
}

/**
 * Compares two values, performing natural/content-aware sorting for IPs and numbers.
 */
function naturalCompare(a, b) {
    // 1. Numeric check
    const numA = Number(a);
    const numB = Number(b);
    if (!isNaN(numA) && !isNaN(numB) && a !== '' && b !== '') {
        return numA - numB;
    }

    // 2. IP check
    const ipPattern = /^(\d{1,3}\.){3}\d{1,3}$/;
    const isA = ipPattern.test(a);
    const isB = ipPattern.test(b);
    
    if (isA && isB) {
        const octetsA = a.split('.').map(Number);
        const octetsB = b.split('.').map(Number);
        for (let i = 0; i < 4; i++) {
            if (octetsA[i] !== octetsB[i]) {
                return octetsA[i] - octetsB[i];
            }
        }
        return 0;
    }

    // 3. Fallback to localeCompare
    return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' });
}

/**
 * Renders a data table in the search results view.
 */
function renderResultsTable(containerId, headers, data, rowMapper) {
    const container = document.getElementById(containerId);
    if (!container) return;

    // Update count in header
    const countSpan = document.getElementById(`${containerId}-count`);
    if (countSpan) {
        countSpan.innerText = data ? `(${data.length})` : '(0)';
    }

    if (!data || data.length === 0) {
        container.innerHTML = '<p class="text-gray-500">No activity found.</p>';
        return;
    }

    // Initialize sort state for this table if not exists
    if (!tableSortStates[containerId]) {
        tableSortStates[containerId] = { colIndex: 1, direction: -1 }; // Default sort by Count descending
    }

    const sortState = tableSortStates[containerId];
    
    // Sort data
    const sortedData = [...data].sort((a, b) => {
        const valA = rowMapper(a)[sortState.colIndex];
        const valB = rowMapper(b)[sortState.colIndex];
        
        if (typeof valA === 'number') {
            return (valA - valB) * sortState.direction;
        }
        
        return naturalCompare(valA, valB) * sortState.direction;
    });
    
    let html = `<table class="min-w-full divide-y divide-gray-200">
        <thead class="bg-gray-50">
            <tr>
                ${headers.map((h, i) => `
                    <th onclick="sortTable('${containerId}', ${i})" class="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100">
                        ${h} ${sortState.colIndex === i ? (sortState.direction === 1 ? '↑' : '↓') : ''}
                    </th>`).join('')}
            </tr>
        </thead>
        <tbody class="bg-white divide-y divide-gray-200">
            ${sortedData.map(item => {
                const cells = rowMapper(item);
                return `<tr>${cells.map(c => `<td class="px-4 py-2 whitespace-nowrap text-sm text-gray-900">${c}</td>`).join('')}</tr>`;
            }).join('')}
        </tbody>
    </table>`;
    
    container.innerHTML = html;

    // Store the raw data and rowMapper for re-sorting
    container.dataset.rawData = JSON.stringify(data);
    container._rowMapper = rowMapper;
    container._headers = headers;
}

/**
 * Sorts a table by column index.
 */
function sortTable(containerId, colIndex) {
    const container = document.getElementById(containerId);
    const data = JSON.parse(container.dataset.rawData);
    const rowMapper = container._rowMapper;
    const headers = container._headers;

    if (tableSortStates[containerId].colIndex === colIndex) {
        tableSortStates[containerId].direction *= -1;
    } else {
        tableSortStates[containerId].colIndex = colIndex;
        tableSortStates[containerId].direction = 1;
    }

    renderResultsTable(containerId, headers, data, rowMapper);
}

// =============================================================================
// INITIALIZATION
// =============================================================================

loadMissingData();
startIndexStatePolling();

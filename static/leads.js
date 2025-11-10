let currentLeadPage = 1;
let currentQuery = '';
let currentInterest = '';
const perLeadPage = 5; // Adjustable for scalability

const searchInput = document.getElementById('searchInput');
const scoreSelect = document.getElementById('scoreSelect');
const exportBtn = document.getElementById('exportBtn');
const leadsTbody = document.getElementById('leadsTbody');
const LeadprevBtn = document.getElementById('LeadprevBtn');
const LeadnextBtn = document.getElementById('LeadnextBtn');
const pageInfo = document.getElementById('pageInfo');

async function refreshCache() {
    fetchLeads(currentLeadPage, currentQuery, currentInterest);
    const btn = document.getElementById('refreshBtn');
    
    // Disable button and show loading
    btn.disabled = true;
    btn.textContent = 'Refreshing...';
    
    try {
        const response = await fetch('/api/leads/refresh', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                // Add authorization if needed: 'Authorization': 'Bearer your-token'
            },
        });
        
        if (!response.ok) {
            throw new Error(`HTTP error! Status: ${response.status}`);
        }
        
        const data = await response.json();
        
        // Optionally, refetch your leads data here
        // e.g., location.reload(); or call another function to update the table
        
    } catch (error) {
        console.error('Failed to refresh cache:', error);
    } finally {
        // Re-enable button
        btn.disabled = false;
        btn.textContent = 'Refresh Cache';
    }
}

// Attach to button click
document.getElementById('refreshBtn').addEventListener('click', refreshCache);

// Show loading spinner
function showLoading() {
    leadsTbody.innerHTML = `
        <tr>
            <td colspan="7" class="text-center py-8">
                <div class="flex justify-center items-center space-x-2">
                    <div class="animate-spin rounded-full h-6 w-6 border-b-2 border-bg-800"></div>
                    <span class="text-sm text-gray-500 dark:text-gray-400">Loading leads...</span>
                </div>
            </td>
        </tr>
    `;
}

// Debounce utility
function debounce(func, delay) {
    let timeoutId;
    return function (...args) {
        clearTimeout(timeoutId);
        timeoutId = setTimeout(() => func.apply(this, args), delay);
    };
}

// Fetch leads function
async function fetchLeads(page = 1, q = currentQuery, interest = currentInterest) {
    showLoading();
    try {
        const params = new URLSearchParams({
            page: page.toString(),
            per_page: perLeadPage.toString(),
            q: q,
            interest: interest
        });
        const response = await fetch(`/api/leads/?${params}`);
        const data = await response.json();

        leadsTbody.innerHTML = ''; // Clear existing rows

        if (data.sessions && data.sessions.length > 0) {
            data.sessions.forEach(session => {
                const scoreClass = session.interest === 'high' ? 'bg-green-100 dark:bg-green-700 text-green-800 dark:text-green-200' :
                                    session.interest === 'medium' ? 'bg-yellow-100 dark:bg-yellow-700 text-yellow-800 dark:text-yellow-200' :
                                    'bg-gray-100 dark:bg-gray-700 text-gray-800 dark:text-gray-200';
                const scoreText = session.interest.charAt(0).toUpperCase() + session.interest.slice(1);
                const company = session.lead_company || '-';
                const service = session.lead_services || '-';

                const row = `
                    <tr class="hover:bg-gray-50 dark:hover:bg-gray-700/30">
                        <td class="px-6 py-4 whitespace-nowrap text-gray-900 dark:text-white">${session.name || '-'}</td>
                        <td class="px-6 py-4 whitespace-nowrap text-gray-500 dark:text-gray-300">${session.lead_email || '-'}</td>
                        <td class="px-6 py-4 whitespace-nowrap text-gray-500 dark:text-gray-300">${company}</td>
                        <td class="px-6 py-4 whitespace-nowrap text-gray-500 dark:text-gray-300">${service}</td>
                        <td class="px-6 py-4 whitespace-nowrap">
                            <span class="text-xs font-medium px-2.5 py-1 rounded-full ${scoreClass}">${scoreText}</span>
                        </td>
                        <td class="px-6 py-4 whitespace-nowrap text-gray-500 dark:text-gray-300">${session.date_str || '-'}</td>
                        <td class="px-6 py-4 whitespace-nowrap">
                            <button class="text-xs inline-flex items-center gap-2 px-4 py-2 bg-white border border-gray-200 rounded hover:shadow-sm" onclick="openSession(
                            '${session.id}',
                            'view',
                            '${escapeJs(session.name)}',
                            '${escapeJs(session.lead_email)}',
                            '${escapeJs(session.usr_phone)}',
                            '${escapeJs(session.lead_company)}',
                            '${escapeJs(session.mood)}',
                            '${escapeJs(session.verified)}',
                            '${escapeJs(session.confidence)}',
                            '${escapeJs(session.evidence)}',
                            '${escapeJs(session.sources)}',
                            '${escapeJs(session.interest)}',
                            '${escapeJs(session.lead_email_domain || '')}',
                            '${escapeJs(session.lead_role || '')}',
                            '${escapeJs(session.lead_categories || '')}',
                            '${escapeJs(session.lead_services || '')}',
                            '${escapeJs(session.lead_activity || '')}',
                            '${escapeJs(session.lead_timeline || '')}',
                            '${escapeJs(session.lead_budget || '')}',
                            '${escapeJs(session.c_sources)}',
                            '${escapeJs(session.c_images)}',
                            '${escapeJs(session.c_info)}',
                            '${escapeJs(session.c_data)}',
                            '${session.approved}'
                            )">
                            <svg class="w-5 h-5" viewBox="0 0 24 24" fill="none"><path d="M15 12a3 3 0 11-6 0 3 3 0 016 0zM2.458 12C3.732 7.943 7.523 5 12 5s8.268 2.943 9.542 7c-1.274 4.057-5.065 7-9.542 7S3.732 16.057 2.458 12z" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
                            View
                            </button>
                        </td>
                    </tr>
                `;
                leadsTbody.insertAdjacentHTML('beforeend', row);
            });
        } else {
            leadsTbody.innerHTML = '<tr><td colspan="7" class="px-6 py-4 text-center text-gray-500 dark:text-gray-400">No leads found.</td></tr>';
        }

        // Update pagination
        const totalPages = data.pagination ? data.pagination.pages : 1;
        currentLeadPage = page;
        pageInfo.textContent = `Page ${currentLeadPage} of ${totalPages}`;
        LeadprevBtn.disabled = currentLeadPage <= 1;
        LeadnextBtn.disabled = currentLeadPage >= totalPages;
    } catch (error) {
        console.error('Error fetching leads:', error);
        leadsTbody.innerHTML = '<tr><td colspan="7" class="px-6 py-4 text-center text-red-500 dark:text-red-400">Error loading leads. Please try again.</td></tr>';
    }
}

// Search debounce handler
const debouncedSearch = debounce((value) => {
    currentQuery = value;
    currentLeadPage = 1;
    fetchLeads(currentLeadPage, currentQuery, currentInterest);
}, 300);

searchInput.addEventListener('input', (e) => {
    debouncedSearch(e.target.value.trim());
});

scoreSelect.addEventListener('change', (e) => {
    currentInterest = e.target.value;
    currentLeadPage = 1;
    fetchLeads(currentLeadPage, currentQuery, currentInterest);
});

LeadprevBtn.addEventListener('click', () => {
    if (currentLeadPage > 1) {
        currentLeadPage--;
        fetchLeads(currentLeadPage, currentQuery, currentInterest);
    }
});

LeadnextBtn.addEventListener('click', () => {
    const totalPages = parseInt(pageInfo.textContent.split(' of ')[1]) || 1;
    if (currentLeadPage < totalPages) {
        currentLeadPage++;
        fetchLeads(currentLeadPage, currentQuery, currentInterest);
    }
});

exportBtn.addEventListener('click', () => {
    const originalText = exportBtn.innerHTML;
    exportBtn.innerHTML = '<div class="flex items-center space-x-2"><div class="animate-spin rounded-full h-4 w-4 border-b-2 border-white dark:border-gray-900"></div><span>Exporting...</span></div>';
    exportBtn.disabled = true;

    const params = new URLSearchParams({
        q: currentQuery,
        interest: currentInterest,
        format: 'csv'
    });
    // Trigger download by navigating to the URL (FastAPI will handle attachment)
    window.location.href = `/api/leads/?${params}`;

    // Re-enable after a short delay (since download is async)
    setTimeout(() => {
        exportBtn.innerHTML = originalText;
        exportBtn.disabled = false;
    }, 2000);
});

// Initial load (assuming this runs when tab is shown; adjust if needed for tab visibility)
document.addEventListener('DOMContentLoaded', () => {
    fetchLeads(1);
});

// If tab is shown dynamically, call fetchLeads() in your tab switch logic, e.g.:
// document.getElementById('leads-tab').addEventListener('transitionend', () => { if (!document.getElementById('leads-tab').classList.contains('hidden')) fetchLeads(currentLeadPage, currentQuery, currentInterest); });

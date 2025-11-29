let currentLeadPage = 1;
let currentQuery = '';
let currentInterest = '';
const perLeadPage = 5; 

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
                <div class="flex flex-col items-center justify-center py-16">
            <div class="animate-spin rounded-full h-8 w-8 border-[4px] border-gray-900 border-t-transparent"></div>
            <p class="mt-4 text-gray-500 text-sm">Loading sessions...</p>
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

                const verifyButtonHtml = session.verified === "true"
                ? `<button title="Verified" class="text-sm font-medium text-white flex items-center gap-1 rounded-md px-2 py-1 " disabled aria-disabled="true">
                        <svg class="w-4 h-4 text-blue-500" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path fill-rule="evenodd" clip-rule="evenodd" d="M9.5924 3.20027C9.34888 3.4078 9.22711 3.51158 9.09706 3.59874C8.79896 3.79854 8.46417 3.93721 8.1121 4.00672C7.95851 4.03705 7.79903 4.04977 7.48008 4.07522C6.6787 4.13918 6.278 4.17115 5.94371 4.28923C5.17051 4.56233 4.56233 5.17051 4.28923 5.94371C4.17115 6.278 4.13918 6.6787 4.07522 7.48008C4.04977 7.79903 4.03705 7.95851 4.00672 8.1121C3.93721 8.46417 3.79854 8.79896 3.59874 9.09706C3.51158 9.22711 3.40781 9.34887 3.20027 9.5924C2.67883 10.2043 2.4181 10.5102 2.26522 10.8301C1.91159 11.57 1.91159 12.43 2.26522 13.1699C2.41811 13.4898 2.67883 13.7957 3.20027 14.4076C3.40778 14.6511 3.51158 14.7729 3.59874 14.9029C3.79854 15.201 3.93721 15.5358 4.00672 15.8879C4.03705 16.0415 4.04977 16.201 4.07522 16.5199C4.13918 17.3213 4.17115 17.722 4.28923 18.0563C4.56233 18.8295 5.17051 19.4377 5.94371 19.7108C6.278 19.8288 6.6787 19.8608 7.48008 19.9248C7.79903 19.9502 7.95851 19.963 8.1121 19.9933C8.46417 20.0628 8.79896 20.2015 9.09706 20.4013C9.22711 20.4884 9.34887 20.5922 9.5924 20.7997C10.2043 21.3212 10.5102 21.5819 10.8301 21.7348C11.57 22.0884 12.43 22.0884 13.1699 21.7348C13.4898 21.5819 13.7957 21.3212 14.4076 20.7997C14.6511 20.5922 14.7729 20.4884 14.9029 20.4013C15.201 20.2015 15.5358 20.0628 15.8879 19.9933C16.0415 19.963 16.201 19.9502 16.5199 19.9248C17.3213 19.8608 17.722 19.8288 18.0563 19.7108C18.8295 19.4377 19.4377 18.8295 19.7108 18.0563C19.8288 17.722 19.8608 17.3213 19.9248 16.5199C19.9502 16.201 19.963 16.0415 19.9933 15.8879C20.0628 15.5358 20.2015 15.201 20.4013 14.9029C20.4884 14.7729 20.5922 14.6511 20.7997 14.4076C21.3212 13.7957 21.5819 13.4898 21.7348 13.1699C22.0884 12.43 22.0884 11.57 21.7348 10.8301C21.5819 10.5102 21.3212 10.2043 20.7997 9.5924C20.5922 9.34887 20.4884 9.22711 20.4013 9.09706C20.2015 8.79896 20.0628 8.46417 19.9933 8.1121C19.963 7.95851 19.9502 7.79903 19.9248 7.48008C19.8608 6.6787 19.8288 6.278 19.7108 5.94371C19.4377 5.17051 18.8295 4.56233 18.0563 4.28923C17.722 4.17115 17.3213 4.13918 16.5199 4.07522C16.201 4.04977 16.0415 4.03705 15.8879 4.00672C15.5358 3.93721 15.201 3.79854 14.9029 3.59874C14.7729 3.51158 14.6511 3.40781 14.4076 3.20027C13.7957 2.67883 13.4898 2.41811 13.1699 2.26522C12.43 1.91159 11.57 1.91159 10.8301 2.26522C10.5102 2.4181 10.2043 2.67883 9.5924 3.20027ZM16.3735 9.86314C16.6913 9.5453 16.6913 9.03 16.3735 8.71216C16.0557 8.39433 15.5403 8.39433 15.2225 8.71216L10.3723 13.5624L8.77746 11.9676C8.45963 11.6498 7.94432 11.6498 7.62649 11.9676C7.30866 12.2854 7.30866 12.8007 7.62649 13.1186L9.79678 15.2889C10.1146 15.6067 10.6299 15.6067 10.9478 15.2889L16.3735 9.86314Z" fill="currentColor"/></svg>
                        <span class="sr-only">Verified</span>
                    </button>`
                : ``;

                const row = `
                    <tr class="hover:bg-gray-50 dark:hover:bg-gray-700/30">
                        <td class="px-6 flex items-center py-4 whitespace-nowrap text-gray-900 dark:text-white">${session.name || '-'}${verifyButtonHtml}</td>
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

exportBtn.addEventListener('click', async () => {
    const originalText = exportBtn.innerHTML;
    
    // Update UI to show loading
    exportBtn.innerHTML = `
        <div class="flex items-center space-x-2">
            <div class="animate-spin rounded-full h-4 w-4 border-b-2 border-white dark:border-gray-900"></div>
            <span>Exporting...</span>
        </div>
    `;
    exportBtn.disabled = true;

    console.log('[Export] Starting CSV export...');
    console.log('[Export] Query params:', { 
        q: currentQuery, 
        interest: currentInterest, 
        format: 'csv' 
    });

    try {
        const params = new URLSearchParams({
            q: currentQuery || '',
            interest: currentInterest || '',
            format: 'csv'
        });

        const url = `/api/leads/?${params}`;
        console.log('[Export] Fetching from:', url);

        const response = await fetch(url, {
            method: 'GET',
            headers: {
                'Accept': 'text/csv',
            },
        });

        console.log('[Export] Response status:', response.status);

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        // Get filename from Content-Disposition if available, otherwise fallback
        const contentDisposition = response.headers.get('Content-Disposition');
        let filename = 'leads.csv';
        if (contentDisposition && contentDisposition.includes('filename=')) {
            const match = contentDisposition.match(/filename="?([^"]+)"?/);
            if (match) filename = match[1];
        }
        console.log('[Export] Suggested filename:', filename);

        const blob = await response.blob();
        console.log('[Export] Blob received:', blob);

        // Create download link and trigger it
        const downloadUrl = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = downloadUrl;
        a.download = filename;
        document.body.appendChild(a);
        a.click();

        // Cleanup
        window.URL.revokeObjectURL(downloadUrl);
        document.body.removeChild(a);

        console.log('[Export] CSV download triggered successfully!');
        
        // Show success state briefly
        exportBtn.innerHTML = '<span>Exported</span>';
        setTimeout(() => {
            exportBtn.innerHTML = originalText;
            exportBtn.disabled = false;
        }, 1500);

    } catch (error) {
        console.error('[Export] Export failed:', error);
        
        // Show error state
        exportBtn.innerHTML = '<span>✕ Failed</span>';
        exportBtn.classList.add('bg-red-600');

        setTimeout(() => {
            exportBtn.innerHTML = originalText;
            exportBtn.disabled = false;
            exportBtn.classList.remove('bg-red-600');
        }, 3000);

        alert('Export failed. Check console for details.');
    }
});


let activeOnly = false;
let currentWs = null;
let reconnectAttempts = 0;
const maxReconnectAttempts = 5;
let currentSessionId = null;
let currentMode = null;
let currentWsUrl = null;
let currentPage = 1;
let perPage = 5;
let totalPages = 1;
let totalSessions = 0;
let sessionRefreshInterval;
let lastSentContent = null;
let currentUserData = {};
const svgIcons = {
    name: '<svg class="w-4 h-4 text-gray-500" viewBox="0 0 24 24" fill="none"><path d="M20 21V19C20 17.9391 19.5786 16.9217 18.8284 16.1716C18.0783 15.4214 17.0609 15 16 15H8C6.93913 15 5.92172 15.4214 5.17157 16.1716C4.42143 16.9217 4 17.9391 4 19V21" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M12 11C14.2091 11 16 9.20914 16 7C16 4.79086 14.2091 3 12 3C9.79086 3 8 4.79086 8 7C8 9.20914 9.79086 11 12 11Z" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    email: '<svg class="w-4 h-4 text-gray-500" viewBox="0 0 24 24" fill="none"><path d="M4 4H20C21.1 4 22 4.9 22 6V18C22 19.1 21.1 20 20 20H4C2.9 20 2 19.1 2 18V6C2 4.9 2.9 4 4 4Z" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M22 6L12 13L2 6" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    phone: '<svg class="w-4 h-4 text-gray-500" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M3 5.5C3 14.0604 9.93959 21 18.5 21C18.8862 21 19.2691 20.9859 19.6483 20.9581C20.0834 20.9262 20.3009 20.9103 20.499 20.7963C20.663 20.7019 20.8185 20.5345 20.9007 20.364C21 20.1582 21 19.9181 21 19.438V16.6207C21 16.2169 21 16.015 20.9335 15.842C20.8749 15.6891 20.7795 15.553 20.6559 15.4456C20.516 15.324 20.3262 15.255 19.9468 15.117L16.74 13.9509C16.2985 13.7904 16.0777 13.7101 15.8683 13.7237C15.6836 13.7357 15.5059 13.7988 15.3549 13.9058C15.1837 14.0271 15.0629 14.2285 14.8212 14.6314L14 16C11.3501 14.7999 9.2019 12.6489 8 10L9.36863 9.17882C9.77145 8.93713 9.97286 8.81628 10.0942 8.64506C10.2012 8.49408 10.2643 8.31637 10.2763 8.1317C10.2899 7.92227 10.2096 7.70153 10.0491 7.26005L8.88299 4.05321C8.745 3.67376 8.67601 3.48403 8.55442 3.3441C8.44701 3.22049 8.31089 3.12515 8.15802 3.06645C7.98496 3 7.78308 3 7.37932 3H4.56201C4.08188 3 3.84181 3 3.63598 3.09925C3.4655 3.18146 3.29814 3.33701 3.2037 3.50103C3.08968 3.69907 3.07375 3.91662 3.04189 4.35173C3.01413 4.73086 3 5.11378 3 5.5Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    company: '<svg class="w-4 h-4 text-gray-500" viewBox="0 0 24 24" fill="none"><path d="M4 7H20M4 12H20M4 17H20M6 17V7M18 17V7" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};


function showPopup(message, type = 'info', duration = 3000) {
    let popup = document.getElementById('dynamicNotification');

    if (!popup) {
        popup = document.createElement('div');
        popup.id = 'dynamicNotification';
        popup.classList.add('dynamic-popup-container');
        document.body.appendChild(popup);
    }
    popup.className = 'dynamic-popup-container';
    popup.classList.add(type);
    popup.textContent = message;
    requestAnimationFrame(() => {
        popup.classList.add('show');
    });

    setTimeout(() => {

        popup.classList.remove('show');
        setTimeout(() => {
            if (popup && !popup.classList.contains('show')) {
                popup.remove();
            }
        }, 400); 

    }, duration);
}

async function loadSessions(page = currentPage) {
    currentPage = page;
    const url = `/api/sessions/?active=${activeOnly}&page=${currentPage}&per_page=${perPage}`;
    try {
    const response = await fetch(url);
    const data = await response.json();
    const sessions = data.sessions || [];
    const pagination = data.pagination || {};
    totalSessions = pagination.total || 0;
    totalPages = pagination.pages || 1;
    const listDiv = document.getElementById('sessionsList');
    if (totalSessions === 0) {
        listDiv.innerHTML = '<div class="p-6 text-center text-gray-500">No sessions found.</div>';
        document.getElementById('paginationControls').style.display = 'none';
        return;
    }
    if (!sessions || sessions.length === 0) {
        listDiv.innerHTML = '<div class="p-6 text-center text-gray-500">No sessions found for this page.</div>';
        updatePaginationUI();
        return;
    }
    let rows = sessions.map(session => {
        const idShort = session.id ? session.id.substring(0,8) + '...' : '—';
        let actions = `<button class="text-xs inline-flex items-center gap-2 px-4 py-2 bg-white border border-gray-200 rounded hover:shadow-sm" onclick="openSession(
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
        '${escapeJs(session.research_data)}',
        '${session.approved}'
        )">
        <svg class="w-5 h-5" viewBox="0 0 24 24" fill="none"><path d="M15 12a3 3 0 11-6 0 3 3 0 016 0zM2.458 12C3.732 7.943 7.523 5 12 5s8.268 2.943 9.542 7c-1.274 4.057-5.065 7-9.542 7S3.732 16.057 2.458 12z" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
        View
        </button>`;
    
        if (session.status === 'active') {
        actions += ` <button class="text-xs inline-flex items-center gap-2 px-4 py-2 bg-gray-800 text-white rounded hover:bg-gray-800/90" onclick="openSession(
            '${session.id}',
            'control',
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
            '${escapeJs(session.research_data)}',
            '${session.approved}'
        )">
            <svg class="w-5 h-5" viewBox="0 0 24 24" fill="white"><path d="M9 7v10l7-5-7-5z" stroke="none" /></svg>
            Control
        </button>`;
        }
    
        return `
        <tr class="session-row hover:bg-background-light dark:hover:bg-background-dark">
            <td class="px-6 py-4 whitespace-nowrap">
            <div class="font-medium text-text-primary-light dark:text-text-primary-dark">${session.name || 'Anonymous'}</div>
            <div class="text-text-secondary-light dark:text-text-secondary-dark">${session.lead_email || 'No email'}</div>
            </td>
            <td class="px-6 py-4 whitespace-nowrap text-text-secondary-light dark:text-text-secondary-dark">${session.lead_company || '-'}</td>
            <td class="px-6 py-4 whitespace-nowrap text-text-secondary-light dark:text-text-secondary-dark">${session.interest}</td>
            <td class="px-6 py-4 whitespace-nowrap">
            <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-gray-100 dark:bg-gray-700 text-text-secondary-light dark:text-text-secondary-dark">
                <span class="material-symbols-outlined !text-sm !font-semibold ${session.status === 'active' ? 'text-green-500' : 'text-gray-500'}">${session.status === 'active' ? 'check' : 'schedule'}</span>
                ${session.status.charAt(0).toUpperCase() + session.status.slice(1)}
            </span>
            </td>
            <td class="px-6 py-4 whitespace-nowrap text-text-secondary-light dark:text-text-secondary-dark">${session.mood || '—'}</td>
            <td class="px-6 py-4 whitespace-nowrap text-text-secondary-light dark:text-text-secondary-dark">${new Date(session.created_at).toLocaleString()}</td>
            <td class="px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
            ${actions}
            </td>
        </tr>
        `;
    }).join('');
    const tableHtml = `
            <div class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden">
            <table class="w-full text-sm text-left">
            <thead class="bg-gray-50 dark:bg-gray-700/50">
            <tr class="border-b border-border-light dark:border-border-dark">
                <th class="px-6 py-4 text-left text-xs font-medium text-text-secondary-light dark:text-text-secondary-dark uppercase tracking-wider">Lead</th>
                <th class="px-6 py-4 text-left text-xs font-medium text-text-secondary-light dark:text-text-secondary-dark uppercase tracking-wider">Company</th>
                <th class="px-6 py-4 text-left text-xs font-medium text-text-secondary-light dark:text-text-secondary-dark uppercase tracking-wider">Score</th>
                <th class="px-6 py-4 text-left text-xs font-medium text-text-secondary-light dark:text-text-secondary-dark uppercase tracking-wider">Status</th>
                <th class="px-6 py-4 text-left text-xs font-medium text-text-secondary-light dark:text-text-secondary-dark uppercase tracking-wider">Mood</th>
                <th class="px-6 py-4 text-left text-xs font-medium text-text-secondary-light dark:text-text-secondary-dark uppercase tracking-wider">Last Activity</th>
                <th class="px-6 py-4 text-left text-xs font-medium text-text-secondary-light dark:text-text-secondary-dark uppercase tracking-wider">Actions</th>
            </tr>
            </thead>
            <tbody class="bg-white divide-y divide-gray-100">
            ${rows}
            </tbody>
        </table>
        </div>
    `;
    listDiv.innerHTML = tableHtml;
    updatePaginationUI();
    } catch (err) {
    document.getElementById('sessionsList').innerHTML = '<div class="p-6 text-red-600">Failed to load sessions</div>';

    document.getElementById('paginationControls').style.display = 'none';
    }
}
function updatePaginationUI() {
    const infoEl = document.getElementById('paginationInfo');
    const prevBtn = document.getElementById('prevPageBtn');
    const nextBtn = document.getElementById('nextPageBtn');
    const paginationControls = document.getElementById('paginationControls');
    paginationControls.style.display = 'flex';
    infoEl.textContent = `Page ${currentPage} of ${totalPages} (${totalSessions} total)`;
    prevBtn.disabled = currentPage <= 1;
    nextBtn.disabled = currentPage >= totalPages;
}
function changePage(delta) {
    const newPage = currentPage + delta;
    if (newPage >= 1 && newPage <= totalPages) {
    loadSessions(newPage);
    }
}
function toggleActive() {
    activeOnly = !activeOnly;
    const allBtn = document.getElementById('allSessionsBtn');
    const activeBtn = document.getElementById('activeOnlyBtn');
    if (activeOnly) {
    allBtn.classList.remove('active');
    allBtn.classList.add('inactive');
    activeBtn.classList.remove('inactive');
    activeBtn.classList.add('active');
    } else {
    activeBtn.classList.remove('active');
    activeBtn.classList.add('inactive');
    allBtn.classList.remove('inactive');
    allBtn.classList.add('active');
    }
    currentPage = 1;
    loadSessions();
}
function escapeJs(s) {
    if (!s) return '';
    return s.replace(/'/g, "\\'").replace(/"/g,'&quot;').replace(/\n/g,' ');
}

async function approveSession(id) {
    try {
      const res = await fetch(`/api/approve/?session_id=${id}`, {
        method: "POST",
      });
  
      if (!res.ok) throw new Error("Failed to approve session");
  
      const data = await res.json();
      showPopup(data.message || "Session approved!");
      
      // Optional: update UI
      const btn = document.getElementById("approveBtn");
      if (btn) {
        btn.innerText = "Approved";
        btn.disabled = true;
        btn.classList.add("opacity-60", "cursor-not-allowed");
      }
    } catch (err) {
      showPopup("Could not approve session");

    }
  }
  
  function downloadResearchAsTxt() {
    // Get the modal content element
    const contentElement = document.getElementById('researchModalContent');
    
    // Check if element exists
    if (!contentElement) {

        return;
    }
    
    // Extract the text content (strips HTML, keeps only plain text)
    const textContent = contentElement.innerText || contentElement.textContent;
    
    // Create a Blob with the text content
    const blob = new Blob([textContent], { type: 'text/plain;charset=utf-8' });
    
    // Create a temporary URL for the Blob
    const url = URL.createObjectURL(blob);
    
    // Create a temporary anchor element to trigger download
    const a = document.createElement('a');
    a.href = url;
    a.download = `research-results-${new Date().toISOString().split('T')[0]}.txt`; // e.g., research-results-2025-11-11.txt
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    
    // Clean up the URL
    URL.revokeObjectURL(url);
    
}

// Example: Attach to the "Download Report" button in the modal
document.addEventListener('DOMContentLoaded', function() {
    const downloadBtn = document.querySelector('#researchModal .bg-indigo-600'); // Target the indigo button
    if (downloadBtn) {
        downloadBtn.addEventListener('click', downloadResearchAsTxt);
    }
});

  function renderUserDetails() {
    const { name, email, phone, company, mood, verified, confidence, evidence, sources, interest,
            lead_email_domain, lead_role, lead_categories, lead_services, lead_activity, lead_timeline, lead_budget, id, c_sources, c_images, c_info, c_data,research_data,approved } = currentUserData;

    window.currentResearchData = research_data;        
    const verifyButtonHtml = verified === "true"
    ? `<button id="verifyBtn" title="Verified" class="text-sm font-medium text-white flex items-center gap-1 rounded-md px-2 py-1 " disabled aria-disabled="true">
            <svg class="w-4 h-4 text-blue-500" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path fill-rule="evenodd" clip-rule="evenodd" d="M9.5924 3.20027C9.34888 3.4078 9.22711 3.51158 9.09706 3.59874C8.79896 3.79854 8.46417 3.93721 8.1121 4.00672C7.95851 4.03705 7.79903 4.04977 7.48008 4.07522C6.6787 4.13918 6.278 4.17115 5.94371 4.28923C5.17051 4.56233 4.56233 5.17051 4.28923 5.94371C4.17115 6.278 4.13918 6.6787 4.07522 7.48008C4.04977 7.79903 4.03705 7.95851 4.00672 8.1121C3.93721 8.46417 3.79854 8.79896 3.59874 9.09706C3.51158 9.22711 3.40781 9.34887 3.20027 9.5924C2.67883 10.2043 2.4181 10.5102 2.26522 10.8301C1.91159 11.57 1.91159 12.43 2.26522 13.1699C2.41811 13.4898 2.67883 13.7957 3.20027 14.4076C3.40778 14.6511 3.51158 14.7729 3.59874 14.9029C3.79854 15.201 3.93721 15.5358 4.00672 15.8879C4.03705 16.0415 4.04977 16.201 4.07522 16.5199C4.13918 17.3213 4.17115 17.722 4.28923 18.0563C4.56233 18.8295 5.17051 19.4377 5.94371 19.7108C6.278 19.8288 6.6787 19.8608 7.48008 19.9248C7.79903 19.9502 7.95851 19.963 8.1121 19.9933C8.46417 20.0628 8.79896 20.2015 9.09706 20.4013C9.22711 20.4884 9.34887 20.5922 9.5924 20.7997C10.2043 21.3212 10.5102 21.5819 10.8301 21.7348C11.57 22.0884 12.43 22.0884 13.1699 21.7348C13.4898 21.5819 13.7957 21.3212 14.4076 20.7997C14.6511 20.5922 14.7729 20.4884 14.9029 20.4013C15.201 20.2015 15.5358 20.0628 15.8879 19.9933C16.0415 19.963 16.201 19.9502 16.5199 19.9248C17.3213 19.8608 17.722 19.8288 18.0563 19.7108C18.8295 19.4377 19.4377 18.8295 19.7108 18.0563C19.8288 17.722 19.8608 17.3213 19.9248 16.5199C19.9502 16.201 19.963 16.0415 19.9933 15.8879C20.0628 15.5358 20.2015 15.201 20.4013 14.9029C20.4884 14.7729 20.5922 14.6511 20.7997 14.4076C21.3212 13.7957 21.5819 13.4898 21.7348 13.1699C22.0884 12.43 22.0884 11.57 21.7348 10.8301C21.5819 10.5102 21.3212 10.2043 20.7997 9.5924C20.5922 9.34887 20.4884 9.22711 20.4013 9.09706C20.2015 8.79896 20.0628 8.46417 19.9933 8.1121C19.963 7.95851 19.9502 7.79903 19.9248 7.48008C19.8608 6.6787 19.8288 6.278 19.7108 5.94371C19.4377 5.17051 18.8295 4.56233 18.0563 4.28923C17.722 4.17115 17.3213 4.13918 16.5199 4.07522C16.201 4.04977 16.0415 4.03705 15.8879 4.00672C15.5358 3.93721 15.201 3.79854 14.9029 3.59874C14.7729 3.51158 14.6511 3.40781 14.4076 3.20027C13.7957 2.67883 13.4898 2.41811 13.1699 2.26522C12.43 1.91159 11.57 1.91159 10.8301 2.26522C10.5102 2.4181 10.2043 2.67883 9.5924 3.20027ZM16.3735 9.86314C16.6913 9.5453 16.6913 9.03 16.3735 8.71216C16.0557 8.39433 15.5403 8.39433 15.2225 8.71216L10.3723 13.5624L8.77746 11.9676C8.45963 11.6498 7.94432 11.6498 7.62649 11.9676C7.30866 12.2854 7.30866 12.8007 7.62649 13.1186L9.79678 15.2889C10.1146 15.6067 10.6299 15.6067 10.9478 15.2889L16.3735 9.86314Z" fill="currentColor"/></svg>
            <span class="sr-only">Verified</span>
        </button>`
    : `<button id="verifyBtn" title="Verify Identity" class="ml-2 inline-flex items-center gap-1.5 px-[15px] py-[4px] rounded-full text-sm font-medium bg-[#111827] text-white focus:outline-none focus:ring-1 focus:ring-blue-500 focus:ring-offset-1 transition-all duration-200" aria-pressed="false">
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
            <span class="verify-text">Verify</span>
        </button>`;

    function markdownToHtml(text) {
        if (!text) return '';
        text = text.replace(/^######\s+(.*)$/gm, '<h6>$1</h6>')
            .replace(/^#####\s+(.*)$/gm, '<h5>$1</h5>')
            .replace(/^####\s+(.*)$/gm, '<h4>$1</h4>')
            .replace(/^###\s+(.*)$/gm, '<h3>$1</h3>')
            .replace(/^##\s+(.*)$/gm, '<h2>$1</h2>')
            .replace(/^#\s+(.*)$/gm, '<h1>$1</h1>');
        // Formatting
        text = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
            .replace(/\*(.*?)\*/g, '<em>$1</em>')
            .replace(/~~(.*?)~~/g, '<del>$1</del>')
            .replace(/__(.*?)__/g, '<u>$1</u>');
        return text;
    }

    // Function to get a teaser (first ~200 chars, truncated nicely)
    function getTeaser(text) {
        if (!text || text.trim().length === 0) return '';
        const maxLength = 200;
        let teaser = text.substring(0, maxLength);
        // Truncate at last space to avoid cutting words
        const lastSpace = teaser.lastIndexOf(' ');
        if (lastSpace > 0 && lastSpace < maxLength - 10) {
            teaser = teaser.substring(0, lastSpace);
        }
        return teaser + '...';
    }

    // Generate the teaser button HTML (using a class for event listener instead of inline onclick)
    let ResearchInfoHtml = '';
    if (research_data && research_data.trim()) {
        const teaserText = getTeaser(research_data);
        ResearchInfoHtml = `
        <div class="research-teaser mt-3 p-4 bg-slate-50/90  rounded-xl border border-slate-200/60 shadow-sm cursor-pointer group  transition-all duration-300 ease-out overflow-hidden">
            <h6 class="text-sm font-bold text-slate-800 mb-2 flex items-center group-hover:text-blue-700 transition-colors duration-200">
            <svg class="w-5 h-5 mr-2 text-slate-500 group-hover:text-blue-500 transition-colors duration-200" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <path d="M12.2429 6.18353L8.55917 8.27415C7.72801 8.74586 7.31243 8.98172 7.20411 9.38603C7.09579 9.79034 7.33779 10.2024 7.82179 11.0264L8.41749 12.0407C8.88853 12.8427 9.12405 13.2437 9.51996 13.3497C9.91586 13.4558 10.3203 13.2263 11.1292 12.7672L14.8646 10.6472M7.05634 9.72257L3.4236 11.7843C2.56736 12.2702 2.13923 12.5132 2.02681 12.9256C1.91438 13.3381 2.16156 13.7589 2.65591 14.6006C3.15026 15.4423 3.39744 15.8631 3.81702 15.9736C4.2366 16.0842 4.66472 15.8412 5.52096 15.3552L9.1537 13.2935M21.3441 5.18488L20.2954 3.39939C19.8011 2.55771 19.5539 2.13687 19.1343 2.02635C18.7147 1.91584 18.2866 2.15881 17.4304 2.64476L13.7467 4.73538C12.9155 5.20709 12.4999 5.44294 12.3916 5.84725C12.2833 6.25157 12.5253 6.6636 13.0093 7.48766L14.1293 9.39465C14.6004 10.1966 14.8359 10.5976 15.2318 10.7037C15.6277 10.8098 16.0322 10.5802 16.841 10.1212L20.5764 8.00122C21.4326 7.51527 21.8608 7.2723 21.9732 6.85985C22.0856 6.44741 21.8384 6.02657 21.3441 5.18488Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>
                    <path d="M12 12.5L16 22" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
                    <path d="M12 12.5L8 22" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
                </svg>    
            Deep Research Result
            </h6>
            <div class="relative overflow-hidden max-h-24 transition-all duration-500 ease-out">
                <p class="text-sm text-slate-600 leading-tight transition-all duration-300">${teaserText}</p>
                <div class="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-slate-50 to-transparent pointer-events-none opacity-70 h-4 transition-all duration-500 ease-out"></div>
                <div class="absolute bottom-0 left-0 right-0 h-12 bg-gradient-to-t from-slate-50/30 via-slate-50/70 to-transparent pointer-events-none opacity-100 transition-all duration-500 ease-out delay-200"></div>
            </div>
        </div>
        `;
    }

    let ConsultationHtml = `
    <div class="consultations-wrapper mt-3 p-4 bg-slate-50/90 rounded-xl border border-slate-200/60 shadow-sm">
        <h6 class="text-sm font-bold text-slate-800 mb-3 flex items-center">
            <svg class="w-5 h-5 mr-2 text-slate-500" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <path d="M9 20H6C3.79086 20 2 18.2091 2 16V7C2 4.79086 3.79086 3 6 3H17C19.2091 3 21 4.79086 21 7V10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    <path d="M8 2V4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    <path d="M15 2V4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    <path d="M2 8H21" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    <path d="M18.5 15.6429L17 17.1429" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    <circle cx="17" cy="17" r="5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>   
            Scheduled Consultations
        </h6>
        
        <div id="consultation-list-${id}" class="min-h-[60px]">
            <div class="animate-pulse flex space-x-4 p-2">
                <div class="flex-1 space-y-2 py-1">
                    <div class="h-2 bg-slate-200 rounded"></div>
                    <div class="h-2 bg-slate-200 rounded w-5/6"></div>
                </div>
            </div>
        </div>

        <img src="" onerror="window.loadSessionConsultations('${id}')" style="display:none;" />
    </div>
    `;

    const contactSection = `
    <div class="bg-white rounded-lg  overflow-hidden">

        <div class="p-4">
        <div class="flex items-center gap-4 mb-4 pb-3 border-b border-gray-200">
            <div class="relative">
            <div class="rounded-full bg-gradient-to-br uppercase from-gray-100 to-gray-200 border-2 border-gray-300 w-14 h-14 flex items-center justify-center text-xl font-bold text-gray-700 flex-shrink-0 shadow-sm">
                ${(name && name[0]) || '?'}
            </div>
            </div>
            <div class="flex-1 min-w-0">
            <div class="text-base font-semibold text-gray-900 flex items-center mb-1 leading-tight">
                ${name || '<span class="text-gray-500">Not provided</span>'}
                ${verifyButtonHtml}
            </div>
            <div class="text-sm text-gray-500 flex items-center gap-1">
                ${company ? `<span class="font-medium">${company}</span>` : ''}
                ${company && interest ? ' • ' : ''}
                ${interest}
            </div>
            </div>
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
            <div class="flex items-start gap-2.5 p-3 bg-gray-50/80 rounded-md border border-gray-200/50">
            <div class="w-8 h-8 rounded-full bg-white/50 flex items-center justify-center flex-shrink-0">
                ${svgIcons.email}
            </div>
            <div class="flex-1 min-w-0">
                <div class="text-xs font-medium text-gray-500 uppercase tracking-wide mb-0.5">Email Address</div>
                <div class="text-sm text-gray-900 truncate">${email && email.trim() ? email : '<span class="text-gray-400">Not provided</span>'}</div>
            </div>
            </div>

            <div class="flex items-start gap-2.5 p-3 bg-gray-50/80 rounded-md border border-gray-200/50">
            <div class="w-8 h-8 rounded-full bg-white/50 flex items-center justify-center flex-shrink-0">
                ${svgIcons.phone}
            </div>
            <div class="flex-1 min-w-0">
                <div class="text-xs font-medium text-gray-500 uppercase tracking-wide mb-0.5">Phone Number</div>
                <div class="text-sm text-gray-900 truncate">${phone && phone.trim() ? phone : '<span class="text-gray-400">Not provided</span>'}</div>
            </div>
            </div>

            ${lead_email_domain ? `
            <div class="flex items-start gap-2.5 p-3 bg-gray-50/80 rounded-md border border-gray-200/50">
            <div class="w-8 h-8 rounded-full bg-white/50 flex items-center justify-center flex-shrink-0">
                ${svgIcons.company}
            </div>
            <div class="flex-1 min-w-0">
                <div class="text-xs font-medium text-gray-500 uppercase tracking-wide mb-0.5">Email Domain</div>
                <div class="text-sm text-gray-900">${lead_email_domain}</div>
            </div>
            </div>
            ` : ''}

            ${lead_role ? `
            <div class="flex items-start gap-2.5 p-3 bg-gray-50/80 rounded-md border border-gray-200/50">
            <div class="w-8 h-8 rounded-full bg-white/50 flex items-center justify-center flex-shrink-0">
                ${svgIcons.name}
            </div>
            <div class="flex-1 min-w-0">
                <div class="text-xs font-medium text-gray-500 uppercase tracking-wide mb-0.5">Professional Role</div>
                <div class="text-sm text-gray-900">${lead_role}</div>
            </div>
            </div>
            ` : ''}
            
        </div>
        ${ResearchInfoHtml}
        ${ConsultationHtml}
        </div>
    </div>
    `;

    // Company Profile Section
    let companySection = '';
    if (company) {
    let cInfoHtml = '';
    if (c_info && c_info.trim()) {
        cInfoHtml = `
        <div class="mb-3 p-3 bg-gray-50/80 rounded-md border border-gray-200/50">
            <h6 class="text-sm font-semibold text-gray-900 mb-1.5">Company Overview</h6>
            <p class="text-sm text-gray-700 leading-tight">${c_info}</p>
        </div>
        `;
    }

    

    

    let cDataHtml = '';
    if (c_data && c_data !== '{}' && c_data !== 'null' && c_data !== null) {
        try {
        const cDataObj = JSON.parse(c_data);
        const validEntries = Object.entries(cDataObj).filter(([key, value]) => value !== null && value !== '' && value !== undefined);
        if (validEntries.length > 0) {
            cDataHtml = `
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 mb-3">
                ${validEntries.map(([key, value]) => {
                const displayKey = key.charAt(0).toUpperCase() + key.slice(1).replace(/_/g, ' ');
                return `
                    <div class="bg-white/50 p-2.5 rounded-md border border-gray-200/50 backdrop-blur-sm">
                    <div class="text-xs text-gray-500 uppercase tracking-wide font-medium mb-0.5">${displayKey}</div>
                    <div class="text-sm text-gray-900 font-semibold">${value}</div>
                    </div>
                `;
                }).join('')}
            </div>
            `;
        }
        } catch (e) {
        console.warn('Invalid c_data JSON');
        }
    }

    let leadInfoHtml = '';
    if (lead_categories || lead_services || lead_activity || lead_timeline || lead_budget) {
        const leadItems = [];
        if (lead_categories) leadItems.push(`<div class="flex items-start gap-3 py-2 border-b border-gray-100/50 last:border-b-0 hover:bg-gray-50/50 transition-colors duration-200 rounded-lg px-2 cursor-pointer group">
        <div class="w-10 h-10 bg-blue-50 rounded-lg flex items-center justify-center flex-shrink-0 group-hover:bg-blue-100 transition-colors duration-200">
            <svg class="w-4 h-4 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z"></path></svg>
        </div>
        <div class="min-w-0 flex-1">
            <span class="text-xs font-semibold text-gray-600 block mb-1">Categories</span>
            <p class="text-sm font-medium text-gray-900 leading-relaxed">${lead_categories}</p>
        </div>
        </div>`);
    
        if (lead_services) leadItems.push(`<div class="flex items-start gap-3 py-2 border-b border-gray-100/50 last:border-b-0 hover:bg-gray-50/50 transition-colors duration-200 rounded-lg px-2 cursor-pointer group">
        <div class="w-10 h-10 bg-green-50 rounded-lg flex items-center justify-center flex-shrink-0 group-hover:bg-green-100 transition-colors duration-200">
            <svg class="w-4 h-4 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z"></path></svg>
        </div>
        <div class="min-w-0 flex-1">
            <span class="text-xs font-semibold text-gray-600 block mb-1">Services</span>
            <p class="text-sm font-medium text-gray-900 leading-relaxed">${lead_services}</p>
        </div>
        </div>`);
    
        if (lead_activity) leadItems.push(`<div class="flex items-start gap-3 py-2 border-b border-gray-100/50 last:border-b-0 hover:bg-gray-50/50 transition-colors duration-200 rounded-lg px-2 cursor-pointer group">
        <div class="w-10 h-10 bg-purple-50 rounded-lg flex items-center justify-center flex-shrink-0 group-hover:bg-purple-100 transition-colors duration-200">
            <svg class="w-4 h-4 text-purple-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
        </div>
        <div class="min-w-0 flex-1">
            <span class="text-xs font-semibold text-gray-600 block mb-1">Activity</span>
            <p class="text-sm font-medium text-gray-900 leading-relaxed">${lead_activity}</p>
        </div>
        </div>`);
    
        if (lead_timeline) leadItems.push(`<div class="flex items-start gap-3 py-2 border-b border-gray-100/50 last:border-b-0 hover:bg-gray-50/50 transition-colors duration-200 rounded-lg px-2 cursor-pointer group">
        <div class="w-10 h-10 bg-indigo-50 rounded-lg flex items-center justify-center flex-shrink-0 group-hover:bg-indigo-100 transition-colors duration-200">
            <svg class="w-4 h-4 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"></path></svg>
        </div>
        <div class="min-w-0 flex-1">
            <span class="text-xs font-semibold text-gray-600 block mb-1">Timeline</span>
            <p class="text-sm font-medium text-gray-900 leading-relaxed">${lead_timeline}</p>
        </div>
        </div>`);
    
        if (lead_budget) leadItems.push(`<div class="flex items-start gap-3 py-2 border-b border-gray-100/50 last:border-b-0 hover:bg-gray-50/50 transition-colors duration-200 rounded-lg px-2 cursor-pointer group">
        <div class="w-10 h-10 bg-yellow-50 rounded-lg flex items-center justify-center flex-shrink-0 group-hover:bg-yellow-100 transition-colors duration-200">
            <svg class="w-8 h-8 text-yellow-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.2" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1"></path></svg>
        </div>
        <div class="min-w-0 flex-1">
            <span class="text-xs font-semibold text-gray-600 block mb-1">Budget</span>
            <p class="text-sm font-medium text-gray-900 leading-relaxed">${lead_budget}</p>
        </div>
        </div>`); leadInfoHtml = `
        <div class="mb-3">
            <h6 class="text-xs font-semibold text-gray-900 mb-2">Lead Insights</h6>
            <div class="space-y-0 bg-white/50 rounded-md border border-gray-200/50 p-2">${leadItems.join('')}</div>
        </div>
        `;
    }

    companySection = `
        <div class="bg-white/80 backdrop-blur-sm rounded-lg shadow-sm border border-gray-200/50 overflow-hidden">
        <div class="px-[28px] py-2.5 border-b border-gray-200 bg-white">
        <h3 class="text-lg font-semibold text-gray-900 [&::first-letter]:uppercase">
        ${company}
        </h3>
        </div>

        <div class="p-4">
            ${cInfoHtml}
            ${cDataHtml}
            ${leadInfoHtml}
        </div>
        </div>
    `;
    } else {
    companySection = '<div class="text-center py-8 text-gray-500">No company information available.</div>';
    }

    // Identity Verification Section
    let verifiedSection = '<div class="text-center py-8 text-gray-500">No verification data available.</div>';
    if (verified !== "null" && verified !== null) {
    
        const status = verified === "true" ? "Verified" : "Not Verified";
        const statusColor = status === "Verified" ? "text-green-600 bg-green-50/80 border-green-200/50" : "text-red-600 bg-red-50/80 border-red-200/50";
        let confHtml = '';
        if (confidence) {
            confHtml = `<div class="p-3 bg-white/50 rounded-lg border border-gray-200/50 shadow-sm">
                <div class="text-xs font-medium text-gray-500 uppercase tracking-wide mb-1.5">Confidence Level</div>
                <div class="text-base text-gray-900 font-semibold">${confidence}</div>
            </div>`;
        }
        let eviHtml = '';
        if (evidence) {
            eviHtml = `<div class="p-3 bg-white/50 rounded-lg border border-gray-200/50 shadow-sm">
                <div class="text-xs font-medium text-gray-500 uppercase tracking-wide mb-1.5">Verification Evidence</div>
                <div class="text-sm text-gray-700 leading-relaxed">${evidence}</div>
            </div>`;
        }
        let srcHtml = '';
        if (sources && sources !== "[]") {
            try {
                const srcList = JSON.parse(sources);
                srcHtml = `
                    <div class="space-y-2">
                        <div class="text-xs font-medium text-gray-500 uppercase tracking-wide">User Verification Sources</div>
                        <div class="flex flex-wrap gap-2">
                            ${srcList.map(url => {
                                const domain = new URL(url).hostname.replace('www.', '');
                                const favicon = `https://www.google.com/s2/favicons?sz=32&domain=${domain}`;
                                return `
                                    <a href="${url}" target="_blank" rel="noopener noreferrer" 
                                    class="group relative inline-flex items-center px-3 py-2 text-sm font-medium text-gray-700 bg-white/50 border border-gray-200/50 rounded-lg shadow-sm hover:shadow-md hover:bg-white hover:border-gray-300 transition-all duration-200 overflow-hidden">
                                        <div class="flex-shrink-0 w-5 h-5 mr-2">
                                            <img src="${favicon}" alt="${domain}" class="w-5 h-5 rounded-md" onerror="this.src='data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjQiIGhlaWdodD0iMjQiIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPGNpcmNsZSBjeD0iMTIiIGN5PSIxMiIgcj0iMTAiIHN0cm9rZT0iI2U1ZTVlNSIgLz4KPC9zdmc+';">
                                        </div>
                                        <span class="min-w-0 flex-1 truncate">${domain}</span>
                                        <svg class="w-4 h-4 ml-1 text-gray-400 group-hover:text-gray-600 flex-shrink-0 transition-colors duration-200" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"></path>
                                        </svg>
                                    </a>
                                `;
                            }).join('')}
                        </div>
                    </div>
                `;
            } catch (e) {
                console.warn('Invalid sources JSON');
            }
        }
        let ComsrcHtml = '';
        if (c_images && c_images !== "[]") {
            try {
                const ComsrcList = JSON.parse(c_images);
                ComsrcHtml = `
                    <div class="space-y-2">
                        <div class="text-xs font-medium text-gray-500 uppercase tracking-wide">Company Associated Images</div>
                        <div class="flex flex-wrap gap-3">
                            ${ComsrcList.map(imgUrl => {
                                return `
                                    <div class="relative w-24 h-24 rounded-lg overflow-hidden border border-gray-200/50 shadow-sm hover:shadow-lg transition-all duration-200 group cursor-pointer bg-gray-50/50">
                                        <img src="${imgUrl}" alt="Associated Image" class="object-cover w-full h-full group-hover:scale-105 transition-transform duration-300" loading="lazy" />
                                        <a href="${imgUrl}" target="_blank" rel="noopener noreferrer" 
                                        class="absolute inset-0 flex items-center justify-center bg-black/0 group-hover:bg-black/20 transition-all duration-200 opacity-0 group-hover:opacity-100">
                                            <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"></path>
                                            </svg>
                                        </a>
                                    </div>
                                `;
                            }).join('')}
                        </div>
                    </div>
                `;
            } catch (e) {
                console.warn('Invalid images JSON');
            }
        }
        let cSourcesHtml = '';
        if (c_sources && c_sources !== "[]") {
        try {
            const srcList = JSON.parse(c_sources);
            cSourcesHtml = `
            <div class="space-y-3">
                <div class="flex items-center justify-between">
                <div class="text-xs font-semibold text-gray-500 uppercase tracking-wide">
                    Verification Sources
                </div>
                <div class="h-[1px] flex-1 ml-3 bg-gray-200"></div>
                </div>

                <div class="flex items-center gap-3 overflow-x-auto pb-2 -mb-2 hide-scrollbar">
                ${srcList.map((url) => {
                    const domain = new URL(url).hostname.replace('www.', '');
                    const favicon = `https://www.google.com/s2/favicons?sz=64&domain=${domain}`;
                    return `
                    <a href="${url}" target="_blank" rel="noopener noreferrer"
                        class="group flex flex-col items-center justify-center w-14 h-14 bg-white border border-gray-200 rounded-xl
                                shadow-sm hover:border-gray-300 hover:shadow transition-all duration-150 flex-shrink-0">
                        <img src="${favicon}" alt="${domain}" class="w-7 h-7 rounded-md" 
                            onerror="this.src='data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMzIiIGhlaWdodD0iMzIiIHZpZXdCb3g9IjAgMCAzMiAzMiIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPGNpcmNsZSBjeD0iMTYiIGN5PSIxNiIgcj0iMTQiIHN0cm9rZT0iI2U1ZTVlNSIgc3Ryb2tlLXdpZHRoPSIyIi8+CjxsaW5lIHgxPSIxMCIgeTE9IjE2IiB4Mj0iMjIiIHkyPSIxNiIgc3Ryb2tlPSIjZTVlNWU1IiBzdHJva2Utd2lkdGg9IjIiLz4KPGxpbmUgeDE9IjE2IiB5MT0iMTAiIHgyPSIxNiIgeTI9IjIyIiBzdHJva2U9IiNlNWU1ZTUiIHN0cm9rZS13aWR0aD0iMiIvPgo8L3N2Zz4=';">
                    </a>
                    `;
                }).join('')}
                </div>
            </div>

            <style>
                /* Hide scrollbar cleanly */
                .hide-scrollbar::-webkit-scrollbar { display: none; }
                .hide-scrollbar { -ms-overflow-style: none; scrollbar-width: none; }
            </style>
            `;
        } catch (e) {
            console.warn('Invalid c_sources JSON');
        }
        }


        // Build grid items conditionally
        let gridItems = '<div class="grid grid-cols-1 gap-6">';
        if (confHtml) gridItems += `<div class="w-full">${confHtml}</div>`;
        if (eviHtml) gridItems += `<div class="w-full">${eviHtml}</div>`;
        if (srcHtml) gridItems += `<div class="w-full p-4 bg-gray-50/50 rounded-xl border border-gray-200/50"><div class="text-sm text-gray-700">${srcHtml}</div></div>`;
        if (ComsrcHtml) gridItems += `<div class="w-full p-4 bg-gray-50/50 rounded-xl border border-gray-200/50"><div class="text-sm text-gray-700">${ComsrcHtml}</div></div>`;
        if (cSourcesHtml) gridItems += `<div class="w-full p-4 bg-gray-50/50 rounded-xl border border-gray-200/50"><div class="text-sm text-gray-700">${cSourcesHtml}</div></div>`;
        gridItems += '</div>';

        // Only show grid if there are items
        const hasContent = confHtml || eviHtml || srcHtml || ComsrcHtml || cSourcesHtml;
        const gridSection = hasContent ? `<div class="p-6">${gridItems}</div>` : '';

        verifiedSection = `
            <div class=" overflow-hidden">
                <div class="bg-gradient-to-r from-slate-50/80 via-blue-50/50 to-slate-50/80 px-6 py-4 border-b border-gray-200/50">
                    <div class="flex items-center justify-between">
                        <div class="flex items-center gap-3">
                            <div class="p-2 bg-blue-100/80 rounded-xl border border-blue-200/50">
                                <svg class="w-5 h-5 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                                </svg>
                            </div>
                            <h3 class="text-lg font-semibold text-gray-900 tracking-tight">Identity Verification</h3>
                        </div>
                        <span class="inline-flex items-center px-3 py-1.5 ${statusColor} rounded-full text-sm font-semibold shadow-sm border">
                            ${status}
                        </span>
                    </div>
                    
                </div>
                ${gridSection}
            </div>
        `;
    }

    const userInfoHtml = `
    <div class="user-tab-header">
        <button class="user-tab-btn active" data-tab="contact">Contact Information</button>
        <button class="user-tab-btn" data-tab="company">Company Profile</button>
        <button class="user-tab-btn" data-tab="verified">Identity Verification</button>
    </div>
    <div class="user-tab-content">
        <div class="user-tab-panel active" id="contact-panel">${contactSection}</div>
        <div class="user-tab-panel" id="company-panel">${companySection}</div>
        <div class="user-tab-panel" id="verified-panel">${verifiedSection}</div>
    </div>

    `;

    document.getElementById('userDetailsSection').innerHTML = userInfoHtml;

   
    function setupResearchModal() {
        // Append modal if not exists
        if (document.getElementById('researchModal')) return;
        
        const modalHtml = `
        <div id="researchModal" class="fixed inset-0 z-50 hidden overflow-y-auto bg-black/50 backdrop-blur-sm transition-opacity duration-300 ease-out">
    <div class="flex min-h-full items-center justify-center p-4 text-center sm:p-6 lg:p-8">
        <div class="relative w-full max-w-4xl max-h-[90vh] transform overflow-hidden rounded-2xl bg-white shadow-2xl transition-all duration-300 ease-out sm:w-full sm:max-w-5xl">
            <!-- Header -->
            <div class="flex items-center justify-between border-b border-slate-200 bg-slate-50/50 px-6 py-4 backdrop-blur-sm">
                <div class="flex items-center space-x-3">
                    <div class="h-8 w-8 rounded-lg bg-gray-800 shadow-md flex items-center justify-center">
                        <svg class="h-4 w-4 text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M12.2429 6.18353L8.55917 8.27415C7.72801 8.74586 7.31243 8.98172 7.20411 9.38603C7.09579 9.79034 7.33779 10.2024 7.82179 11.0264L8.41749 12.0407C8.88853 12.8427 9.12405 13.2437 9.51996 13.3497C9.91586 13.4558 10.3203 13.2263 11.1292 12.7672L14.8646 10.6472M7.05634 9.72257L3.4236 11.7843C2.56736 12.2702 2.13923 12.5132 2.02681 12.9256C1.91438 13.3381 2.16156 13.7589 2.65591 14.6006C3.15026 15.4423 3.39744 15.8631 3.81702 15.9736C4.2366 16.0842 4.66472 15.8412 5.52096 15.3552L9.1537 13.2935M21.3441 5.18488L20.2954 3.39939C19.8011 2.55771 19.5539 2.13687 19.1343 2.02635C18.7147 1.91584 18.2866 2.15881 17.4304 2.64476L13.7467 4.73538C12.9155 5.20709 12.4999 5.44294 12.3916 5.84725C12.2833 6.25157 12.5253 6.6636 13.0093 7.48766L14.1293 9.39465C14.6004 10.1966 14.8359 10.5976 15.2318 10.7037C15.6277 10.8098 16.0322 10.5802 16.841 10.1212L20.5764 8.00122C21.4326 7.51527 21.8608 7.2723 21.9732 6.85985C22.0856 6.44741 21.8384 6.02657 21.3441 5.18488Z" stroke-linejoin="round"/>
                            <path d="M12 12.5L16 22M12 12.5L8 22" stroke-linecap="round"/>
                        </svg>
                    </div>
                    <h2 class="text-lg font-bold text-slate-900">Deep Research Results</h2>
                </div>
                <button onclick="document.getElementById('researchModal').classList.add('hidden')" class="group flex h-8 w-8 items-center justify-center rounded-full text-slate-400 transition-all hover:bg-slate-100 hover:text-slate-600 focus:outline-none ">
                    <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
                    </svg>
                </button>
            </div>

            <!-- Scrollable Content -->
            <div class="relative max-h-[calc(90vh-8rem)] overflow-y-auto px-6 py-6 lg:px-8">
                <div id="researchModalContent" class="prose prose-slate max-w-none text-[15px] leading-relaxed">
                    <!-- Dynamic research content loads here -->
                </div>
            </div>

            <!-- Footer (optional: add actions if needed) -->
            <div class="flex justify-end space-x-3 border-t border-slate-200 bg-slate-50/50 px-6 py-4 backdrop-blur-sm">
                <button onclick="document.getElementById('researchModal').classList.add('hidden')" class="rounded-lg border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700 shadow-sm transition-all hover:bg-slate-50 focus:outline-none">
                    Close
                </button>
                <button class="rounded-lg bg-gray-800 px-4 py-2 text-sm font-medium text-white shadow-sm transition-all hover:bg-gray-700 focus:outline-none " onclick="downloadResearchAsTxt()" >
                    Download Report
                </button>
            </div>
        </div>
    </div>
</div>
        `;
        document.body.insertAdjacentHTML('beforeend', modalHtml);
        

        const userDetailsSection = document.getElementById('userDetailsSection');
        if (userDetailsSection) {
            userDetailsSection.addEventListener('click', (event) => {
                if (event.target.closest('.research-teaser')) {
                    const contentDiv = document.getElementById('researchModalContent');
                    if (contentDiv) {
                        contentDiv.innerHTML = markdownToHtml(window.currentResearchData || '');
                    }
                    
                    // Show modal
                    const modal = document.getElementById('researchModal');
                    modal.classList.remove('hidden');
                    document.body.style.overflow = 'hidden';
                }
            });
        }
        
        // Modal close listeners (event delegation on document for modal elements)
        document.addEventListener('click', (event) => {
            const modal = document.getElementById('researchModal');
            if (modal && !modal.classList.contains('hidden')) {
                if (event.target === modal || event.target.classList.contains('close-modal')) {
                    modal.classList.add('hidden');
                    document.body.style.overflow = '';
                }
            }
        });
        
        // Prevent modal close when clicking inside modal content
        const modalContent = document.querySelector('#researchModal > div');
        if (modalContent) {
            modalContent.addEventListener('click', (event) => event.stopPropagation());
        }
    }

    // Call setup after rendering
    setupResearchModal();

    // Close on Escape (one-time listener, but check if modal is open)
    const escapeListener = (e) => {
        if (e.key === 'Escape') {
            const modal = document.getElementById('researchModal');
            if (modal && !modal.classList.contains('hidden')) {
                modal.classList.add('hidden');
                document.body.style.overflow = '';
            }
        }
    };
    document.addEventListener('keydown', escapeListener);

    // Attach tab listeners
    const tabBtns = document.querySelectorAll('.user-tab-btn');
    const tabPanels = document.querySelectorAll('.user-tab-panel');
    tabBtns.forEach(btn => {
    btn.addEventListener('click', (e) => {
        const targetTab = e.currentTarget.dataset.tab;
        tabBtns.forEach(b => b.classList.remove('active'));
        tabPanels.forEach(p => p.classList.remove('active'));
        e.currentTarget.classList.add('active');
        document.getElementById(`${targetTab}-panel`).classList.add('active');
    });
    });

    // Attach verify button listener
    const verifyBtn = document.getElementById('verifyBtn');
    if (verifyBtn && !verifyBtn.disabled) {
    verifyBtn.addEventListener('click', handleVerification);
    }

    // Attach deep research button listener
    const deepResearchBtn = document.getElementById('deepResearchBtn');
    if (deepResearchBtn) {
        deepResearchBtn.addEventListener('click', openDeepResearchModal);
    }
}



// 1. Make the status colors object global
const statusColors = {
    'pending': 'bg-yellow-100 text-yellow-800 border-yellow-200',
    'in_progress': 'bg-blue-100 text-blue-800 border-blue-200',
    'completed': 'bg-green-100 text-green-800 border-green-200',
    'cancelled': 'bg-red-100 text-red-800 border-red-200',
    'default': 'bg-slate-100 text-slate-800 border-slate-200'
};

// 2. Make the update function GLOBAL so the injected HTML can find it
window.updateConsultationStatus = async function(consultationId, selectElement) {
    const newStatus = selectElement.value;
    const originalColorClass = selectElement.className; 
    
    // Optimistic UI Update: Change color immediately
    const statusKey = newStatus.toLowerCase().replace(' ', '_');
    const newColorClass = `text-xs font-medium py-1 px-2 rounded-md border focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none cursor-pointer transition-colors ${statusColors[statusKey] || statusColors['default']}`;
    
    selectElement.className = newColorClass;
    selectElement.disabled = true; // Prevent double clicks

    try {
        const response = await fetch(`/consultation/${consultationId}/status`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ new_status: newStatus })
        });

        if (!response.ok) {
            const errText = await response.text();
            throw new Error(`Server responded: ${errText}`);
        }
        showPopup(`Status Updated to ${newStatus}`);
        const result = await response.json();
    } catch (error) {
        console.error("Update failed:", error);
        alert("Failed to update status. Check console for details.");
        // Revert UI on error
        selectElement.className = originalColorClass; 
    } finally {
        selectElement.disabled = false;
    }
};

// 3. The Load function (Make sure this is also accessible or called correctly)
window.loadSessionConsultations = async function(sessionId) {
    const container = document.getElementById(`consultation-list-${sessionId}`);
    if (!container) return;

    try {
        container.innerHTML = '<div class="text-slate-500 text-sm p-4 animate-pulse">Loading schedule...</div>';
        
        const response = await fetch(`/session/${sessionId}/consultations`);
        if (!response.ok) throw new Error('Failed to load');
        
        const consultations = await response.json();

        if (!consultations || consultations.length === 0) {
            container.innerHTML = '<div class="text-slate-400 text-sm p-4 italic">No consultations scheduled.</div>';
            return;
        }

        let html = '<div class="space-y-3">';
        
        consultations.forEach(consultation => {
            const dateObj = new Date(consultation.schedule_time);
            const dateStr = dateObj.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
            const timeStr = dateObj.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
            const currentStatus = consultation.status.toLowerCase().replace(' ', '_');

            html += `
            <div class="flex flex-col sm:flex-row justify-between items-start sm:items-center p-3 bg-white rounded-lg border border-slate-200/60 hover:border-blue-300 transition-colors duration-200">
                <div class="mb-2 sm:mb-0">
                    <div class="flex items-center gap-2 text-slate-800 font-medium">
                        <span>${dateStr} <span class="text-slate-400 text-xs font-normal">|</span> ${timeStr}</span>
                    </div>
                    <div class="text-xs text-slate-500 ml-6 mt-0.5">
                        With: <span class="font-semibold text-slate-600">${consultation.consultant}</span>
                    </div>
                </div>
                <div class="relative">
                     <select 
                        onchange="window.updateConsultationStatus('${consultation.consultation_id}', this)"
                        class="text-xs font-medium py-1 px-2 rounded-md border focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none cursor-pointer transition-colors ${statusColors[currentStatus] || statusColors['default']}"
                    >
                        <option value="pending" ${currentStatus === 'pending' ? 'selected' : ''}>Pending</option>
                        <option value="in_progress" ${currentStatus === 'in_progress' ? 'selected' : ''}>In Progress</option>
                        <option value="completed" ${currentStatus === 'completed' ? 'selected' : ''}>Completed</option>
                        <option value="cancelled" ${currentStatus === 'cancelled' ? 'selected' : ''}>Cancelled</option>
                    </select>
                </div>
            </div>`;
        });

        html += '</div>';
        container.innerHTML = html;

    } catch (error) {
        console.error(error);
        container.innerHTML = '<div class="text-red-400 text-sm p-4">Error loading consultations.</div>';
    }
};

// New function to open Deep Research modal
function openDeepResearchModal() {
    // Create modal HTML with pre-filled form
    const modalHtml = `
    <div id="deepResearchModal" class="fixed inset-0 bg-gray-900/50 backdrop-blur-sm z-50 flex items-center justify-center p-4">
        <div class="bg-white rounded-3xl shadow-xl max-w-[35rem] w-full max-h-[90vh] overflow-y-auto border border-gray-200">
            <div class="p-6">
                <div class="flex items-center justify-between mb-6">
                    <div class="flex items-center space-x-3">
                        <div class="p-2 bg-gray-800 rounded-lg">
                            <svg class="w-5 h-5 text-white" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                                <path d="M12.2429 6.18353L8.55917 8.27415C7.72801 8.74586 7.31243 8.98172 7.20411 9.38603C7.09579 9.79034 7.33779 10.2024 7.82179 11.0264L8.41749 12.0407C8.88853 12.8427 9.12405 13.2437 9.51996 13.3497C9.91586 13.4558 10.3203 13.2263 11.1292 12.7672L14.8646 10.6472M7.05634 9.72257L3.4236 11.7843C2.56736 12.2702 2.13923 12.5132 2.02681 12.9256C1.91438 13.3381 2.16156 13.7589 2.65591 14.6006C3.15026 15.4423 3.39744 15.8631 3.81702 15.9736C4.2366 16.0842 4.66472 15.8412 5.52096 15.3552L9.1537 13.2935M21.3441 5.18488L20.2954 3.39939C19.8011 2.55771 19.5539 2.13687 19.1343 2.02635C18.7147 1.91584 18.2866 2.15881 17.4304 2.64476L13.7467 4.73538C12.9155 5.20709 12.4999 5.44294 12.3916 5.84725C12.2833 6.25157 12.5253 6.6636 13.0093 7.48766L14.1293 9.39465C14.6004 10.1966 14.8359 10.5976 15.2318 10.7037C15.6277 10.8098 16.0322 10.5802 16.841 10.1212L20.5764 8.00122C21.4326 7.51527 21.8608 7.2723 21.9732 6.85985C22.0856 6.44741 21.8384 6.02657 21.3441 5.18488Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
                                <path d="M12 12.5L16 22" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
                                <path d="M12 12.5L8 22" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
                            </svg>
                        </div>
                        <h2 class="text-xl font-semibold text-gray-900">Deep Research</h2>
                    </div>
                    <button id="closeDeepResearchModal" class="text-gray-400 hover:text-gray-600 transition-colors p-1 rounded-lg hover:bg-gray-100">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>
                    </button>
                </div>
                <form id="deepResearchForm">
                    <div class="space-y-5 mb-6">
                            <input type="hidden" name="id" value="${currentUserData.id || ''}">
                        <div>
                            <label for="researchName" class="block text-sm font-medium text-gray-700 mb-2">Username</label>
                            <input type="text" id="researchName" name="name" value="${currentUserData.name || ''}" class="w-full px-3 py-2.5 border border-gray-200 rounded-[.7rem] focus:outline-none   transition-all" required>
                        </div>
                        <div class="flex gap-3">
                        <div class="w-full" >
                            <label for="researchEmail" class="block text-sm font-medium text-gray-700 mb-2">Email</label>
                            <input type="email" id="researchEmail" name="email" value="${currentUserData.email || ''}" class="w-full px-3 py-2.5 border border-gray-200 rounded-[.7rem] focus:outline-none   transition-all" required>
                        </div>
                        <div class="w-full">
                            <label for="researchEmailDomain" class="block text-sm font-medium text-gray-700 mb-2">Email Domain</label>
                            <input type="text" id="researchEmailDomain" name="email_domain" value="${currentUserData.lead_email_domain || ''}" class="w-full px-3 py-2.5 border border-gray-200 rounded-[.7rem] focus:outline-none   transition-all">
                        </div>
                        </div>
                        <div>
                            <label for="researchCompany" class="block text-sm font-medium text-gray-700 mb-2">Company Name</label>
                            <input type="text" id="researchCompany" name="company" value="${currentUserData.company || ''}" class="w-full px-3 py-2.5 border border-gray-200 rounded-[.7rem] focus:outline-none   transition-all">
                        </div>
                        
                        <div>
                            <label for="researchAdditionalInfo" class="block text-sm font-medium text-gray-700 mb-2">Additional Info</label>
                            <textarea id="researchAdditionalInfo" name="additional_info" rows="3" class="w-full px-3 py-2.5 border border-gray-200 rounded-[.7rem] focus:outline-none   transition-all resize-none" placeholder="Any extra details or context for the research..."></textarea>
                        </div>
                    </div>
                    <div class="flex justify-end gap-3 pt-4">
                        <button type="button" id="cancelDeepResearch" class="px-4 py-2.5 text-sm font-medium text-gray-700 bg-gray-100 hover:bg-gray-200 rounded-full transition-colors">Cancel</button>
                        <button type="submit" id="confirmDeepResearch" class="px-4 py-2.5 text-sm font-semibold text-white bg-gray-800 hover:bg-gray-700 rounded-full transition-colors focus:outline-none ">Confirm & Research</button>
                    </div>
                </form>
            </div>
        </div>
    </div>
    `;

    // Append modal to body
    document.body.insertAdjacentHTML('beforeend', modalHtml);

    // Attach event listeners
    const closeModalBtn = document.getElementById('closeDeepResearchModal');
    const cancelBtn = document.getElementById('cancelDeepResearch');
    const confBtn = document.getElementById('confirmDeepResearch');
    const form = document.getElementById('deepResearchForm');
    const modal = document.getElementById('deepResearchModal');

    closeModalBtn.addEventListener('click', closeDeepResearchModal);
    cancelBtn.addEventListener('click', closeDeepResearchModal);
    modal.addEventListener('click', (e) => {
        if (e.target === modal) closeDeepResearchModal();
    });

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const formData = new FormData(form);
        const researchData = {
            id: formData.get('id'),
            name: formData.get('name'),
            email: formData.get('email'),
            company: formData.get('company'),
            email_domain: formData.get('email_domain'),
            additional_info: formData.get('additional_info')
        };
        closeDeepResearchModal();
        showPopup('You will be notified upon research completion');
        try {
            const response = await fetch('/api/deep-research', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    id: researchData.id, // <-- supply session id (match DB)
                    name: researchData.name,
                    email: researchData.email,
                    company: researchData.company,
                    email_domain: researchData.email_domain,
                    additional_info: researchData.additional_info
                })
            });
        
            if (!response.ok) {
                const text = await response.text();
                showPopup('Failed to initiate deep research');
                closeDeepResearchModal();
                return;
            }
        
            const data = await response.json();
            function showNotification(title, body) {
                if (!("Notification" in window)) {
                    showPopup("This browser does not support desktop notification");
                    return;
                }
        
                if (Notification.permission === "granted") {
                    new Notification(title, { body });
                } else if (Notification.permission !== "denied") {
                    Notification.requestPermission().then(permission => {
                        if (permission === "granted") {
                            new Notification(title, { body });
                        } else {
                            showPopup("An error occured while sending notification");
                        }
                    });
                } else {
                    showPopup("An error occured while sending notification.");
                }
            }
        
            // Use a short summary or fallback to raw
            const summary = data.result;
            showPopup('The requested research report is ready');
            showNotification('Research analysis is complete.', summary);
        
            // You can update the UI with data.result if you want
            // e.g., show result in a modal or results area
        } catch (error) {
            showPopup('Failed to initiate deep research. Please try again.');
        } finally {
            // Close modal after submission
            closeDeepResearchModal();
        }

        // Close modal after submission
        closeDeepResearchModal();
    });
}

function closeDeepResearchModal() {
    const modal = document.getElementById('deepResearchModal');
    if (modal) {
        modal.remove();
    }
}
async function handleVerification() {
    showPopup('This Lead is being verified');
    const verifyBtn = document.getElementById('verifyBtn');
    const { name, email, lead_role, company, id } = currentUserData;
    verifyBtn.disabled = true;
    verifyBtn.classList.remove('bg-blue-600', 'hover:bg-blue-700');
    verifyBtn.classList.add('bg-gray-100', 'text-white', 'border', 'border-gray-200', 'cursor-not-allowed');
    verifyBtn.innerHTML = `
    <svg class="animate-spin h-4 w-4 mr-2" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"></path>
    </svg>
    <span class="verify-text">Verifying...</span>
    `;
    try {
    const trimmedName = (name || '').trim();
    const trimmedEmail = (email || '').trim();
    const trimmedCompany = (company || '').trim();
    if (!trimmedName && !trimmedEmail) {
        await showWarning("Verification cannot be accurate without both name and email. Proceed anyway?");
    } else if (!trimmedCompany) {
        await showWarning("Company name is missing. Verification may be less accurate. Continue?");
    }
    const payload = {
        id: id,
        name: trimmedName,
        email: trimmedEmail,
        lead_role: lead_role || '',
        company: trimmedCompany
    };
    const resp = await fetch('/api/verify/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    let respJson = null;
    try { respJson = await resp.clone().json(); } catch (err) { }
    if (resp.ok) {
        showPopup('Verification is complete. Lead data updated');
        currentUserData.verified = respJson.updated_data.verified;
        currentUserData.confidence = respJson.updated_data.confidence;
        currentUserData.evidence = respJson.updated_data.evidence;
        currentUserData.sources = respJson.updated_data.sources;
        verifyBtn.innerHTML = `Verification Done`;
    
        setTimeout(() => renderUserDetails(), 1000);
    } else {
        const errMsg = (respJson && respJson.error) ? respJson.error : `HTTP ${resp.status}`;
        verifyBtn.innerHTML = `<span class="verify-text">Verification failed</span>`;
        verifyBtn.className = 'ml-3 inline-flex items-center gap-2 px-3 py-1 rounded text-xs font-medium bg-red-100 text-red-700 border border-red-200';
        verifyBtn.disabled = false;
        setTimeout(() => {
        verifyBtn.innerHTML = '<span class="verify-text">Retry</span>';
        verifyBtn.className = 'ml-3 inline-flex items-center gap-2 px-3 py-1 rounded text-xs font-medium bg-blue-600 text-white hover:bg-blue-700 focus:outline-none transition';
        }, 1400);
        console.error('Verification failed');
    }
    } catch (err) {
    if (err.message === 'cancelled') {
        verifyBtn.innerHTML = `<span class="verify-text">Verify</span>`;
        verifyBtn.classList.remove('bg-gray-100', 'text-white', 'border', 'cursor-not-allowed');
        verifyBtn.classList.add('bg-blue-600', 'hover:bg-blue-700');
        verifyBtn.disabled = false;
        return;
    }
    verifyBtn.innerHTML = `<span class="verify-text">Verification failed</span>`;
    verifyBtn.className = 'ml-3 inline-flex items-center gap-2 px-3 py-1 rounded text-xs font-medium bg-red-100 text-red-700 border border-red-200';
    verifyBtn.disabled = false;
    setTimeout(() => {
        verifyBtn.innerHTML = '<span class="verify-text">Retry</span>';
        verifyBtn.className = 'ml-3 inline-flex items-center gap-2 px-3 py-1 rounded text-xs font-medium bg-blue-600 text-white hover:bg-blue-700 focus:outline-none transition';
    }, 1400);
    }
}
// --- Global Variables for Project Modal ---
let availableTemplates = [];
let currentCustomTasks = [];

async function openSession(id, mode, name, email, phone, company, mood, verified, confidence, evidence, sources, interest, lead_email_domain, lead_role, lead_categories, lead_services, lead_activity, lead_timeline, lead_budget, c_sources, c_images, c_info, c_data, research_data, approved) {
    if (currentWs) {
        reconnectAttempts = maxReconnectAttempts;
        currentWs.close();
    }
    currentSessionId = id;
    currentMode = mode;
    // Store data globally so modal can access it
    currentUserData = { id, name, email, phone, company, mood, verified, confidence, evidence, sources, interest, lead_email_domain, lead_role, lead_categories, lead_services, lead_activity, lead_timeline, lead_budget, c_sources, c_images, c_info, c_data, research_data, approved };
    
    const isApproved = approved === true || approved === "true";

    // Approve/Lead Button
    const approveButtonHtml = isApproved
        ? `<button id="approveBtn"
            class="bg-white text-gray-700 border border-gray-300 hover:bg-gray-100 px-4 py-1.5 rounded-full text-sm font-medium shadow-sm transition cursor-not-allowed opacity-55" disabled>
            Lead Approved
           </button>`
        : `<button id="approveBtn"
            class="bg-white text-gray-700 border border-gray-300 hover:bg-gray-100 px-4 py-1.5 rounded-full text-sm font-medium shadow-sm transition"
            onclick="approveSession('${id}')">
            Approve Lead
           </button>`;

    // Deep Research Button (SVG omitted for brevity, use yours)
    const DeepRes = `<button title="Run a deep verification" id="deepResearchBtn" onclick="/* your deep research logic */" class="bg-white flex items-center mr-1 gap-1 text-gray-700 border border-gray-300 hover:bg-gray-100 px-4 py-1.5 rounded-full text-sm font-medium shadow-sm transition">
       Deep Research
    </button>`;

    // --- NEW: Export to Project Button ---
    // Only shows if Approved/Lead (optional logic) or always show
    const exportProjectHtml = 
    isApproved
        ? `<button id="exportProjectBtn" onclick="openExportProjectModal()"
        class="bg-indigo-50 text-indigo-700 border border-indigo-200 hover:bg-indigo-100 
            px-4 py-1.5 rounded-full text-sm font-medium shadow-sm transition flex items-center gap-1 ml-1">
            <svg class="w-4 h-4" viewBox="0 0 512 512" version="1.1" xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><title>project</title>
            <g id="Page-1" stroke="none" stroke-width="1" fill="none" fill-rule="evenodd">
            <g id="Combined-Shape" fill="currentColor" transform="translate(64.000000, 34.346667)">
            <path d="M192,7.10542736e-15 L384,110.851252 L384,332.553755 L192,443.405007 L1.42108547e-14,332.553755 L1.42108547e-14,110.851252 L192,7.10542736e-15 Z M42.666,157.654 L42.6666667,307.920144 L170.666,381.82 L170.666,231.555 L42.666,157.654 Z M341.333,157.655 L213.333,231.555 L213.333,381.82 L341.333333,307.920144 L341.333,157.655 Z M192,49.267223 L66.1333333,121.936377 L192,194.605531 L317.866667,121.936377 L192,49.267223 Z">
            </path></g></g></svg>
            Export to Project
            </button>`
        : ``;


    const scheduleConsultationHtml = isApproved
        ? `<button title="Schedule a consultation" id="scheduleConsultationBtn" onclick="openScheduleConsultationModal()" 
            class="bg-indigo-50 mr-1 text-indigo-700 border border-indigo-200 hover:bg-indigo-100 
                   px-4 py-1.5 rounded-full text-sm font-medium shadow-sm transition flex items-center gap-1 ml-1">
                    <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <path d="M9 20H6C3.79086 20 2 18.2091 2 16V7C2 4.79086 3.79086 3 6 3H17C19.2091 3 21 4.79086 21 7V10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    <path d="M8 2V4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    <path d="M15 2V4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    <path d="M2 8H21" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    <path d="M18.5 15.6429L17 17.1429" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    <circle cx="17" cy="17" r="5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
        Schedule Consultation
    </button>`
        : ``;


    document.getElementById('chatTitle').innerHTML = `
        <div class="flex items-center overflow-x-auto no-scrollbar">
            <button id="ModeTrack" class="${mode === 'control' ? 'bg-blue-100 text-blue-800' : 'bg-gray-100 text-gray-600'} mr-2 text-gray-700 border border-gray-300 px-4 py-1.5 rounded-full text-sm font-medium opacity-55" disabled>
                ${mode === 'control' ? 'Control Mode' : 'View Mode'}
            </button>
            ${DeepRes}
            ${approveButtonHtml}
            ${scheduleConsultationHtml} 
            ${exportProjectHtml} 
        </div>
    `;

    document.getElementById('inputArea').style.display = mode === 'control' ? 'block' : 'none';
    document.getElementById('handoverBtn').style.display = mode === 'control' ? 'flex' : 'none';
    
    const modal = document.getElementById('chatModal');
    modal.classList.remove('hidden');
    renderUserDetails(); // Ensure this is defined elsewhere in your code
    currentWsUrl = `/ws/${mode === 'control' ? 'control' : 'view'}/${id}`;
    document.getElementById('messagesContainer').innerHTML = `
    <div id="loading-spinner" class="flex items-center justify-center w-full h-full">
        <div class="animate-spin rounded-full h-10 w-10 border-[4px] border-gray-900 border-t-transparent"></div>
    </div>
    `;
    connectWebSocket();
}

function openScheduleConsultationModal() {

    if (!currentSessionId) {
        showPopup("Please open a session first.");
        return;
    }
    loadConsultants();
    const now = new Date();
    const offset = now.getTimezoneOffset() * 60000; 
    const localIsoString = (new Date(now - offset)).toISOString().slice(0, 16);
    document.getElementById('scheduleTime').value = localIsoString;
    document.getElementById('scheduleConsultationModal').classList.remove('hidden');
    document.getElementById('submitScheduleBtn').onclick = submitConsultationSchedule;
}

// Function to close the modal
function closeScheduleConsultationModal() {
    document.getElementById('scheduleConsultationModal').classList.add('hidden');
}

// Function to fetch and populate the consultant dropdown
async function loadConsultants() {
    const selectElement = document.getElementById('consultantSelect');
    selectElement.innerHTML = '<option value="" disabled selected>Loading...</option>'; // Reset/Loading state

    try {
        const response = await fetch("/consultants"); 
        if (!response.ok) {
            throw new Error(`Failed to load consultants: ${response.status}`);
        }
        
        const consultants = await response.json(); 
        
        selectElement.innerHTML = ''; 

        const defaultOption = document.createElement('option');
        defaultOption.value = '';
        defaultOption.textContent = '--- Select a Consultant ---';
        defaultOption.disabled = true;
        defaultOption.selected = true;
        selectElement.appendChild(defaultOption);

        consultants.forEach(consultant => {
            const option = document.createElement('option');
            option.value = consultant.id;
            const display_name = `${consultant.name} (${consultant.tier})`;
            option.textContent = display_name;
            option.setAttribute('data-consultant-name', display_name);
            selectElement.appendChild(option);
        });

    } catch (error) {
        console.error("Error loading consultants:", error);
        selectElement.innerHTML = '<option value="" disabled selected>Failed to load consultants</option>';
    }
}

async function submitConsultationSchedule() {
    const consultantId = document.getElementById('consultantSelect').value; 
    const scheduleTime = document.getElementById('scheduleTime').value;
    const selectElement = document.getElementById('consultantSelect');

    document.getElementById('submitScheduleBtn').innerText = "Sending";

    if (!consultantId || !scheduleTime) {
        showPopup("Please select a consultant and provide the schedule time.");
        document.getElementById('submitScheduleBtn').innerText = "Confirm Schedule";
        return;
    }

    const selectedOption = selectElement.options[selectElement.selectedIndex];
    const consultantDisplayName = selectedOption.getAttribute('data-consultant-name');
    
    const utcTime = new Date(scheduleTime).toISOString(); 
    
    const payload = {
        session_id: currentSessionId,
        schedule_time: utcTime,
        consultant_id: consultantId, 
        consultant_name_display: consultantDisplayName
    };

    try {
        const response = await fetch("/schedule_consultant", { 
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify(payload),
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || `HTTP error! Status: ${response.status}`);
        }

        const data = await response.json();

        showPopup(`Consultation scheduled with ${data.consultant_name} for ${data.schedule_time}.`); 

    } catch (error) {
        console.error("Error scheduling consultation:", error);
        showPopup(`Failed to schedule consultation`);
    } finally {
        document.getElementById('submitScheduleBtn').innerText = "Confirm Schedule";
        closeScheduleConsultationModal();
    }
}

async function openExportProjectModal() {
    const modal = document.getElementById('projectModal');
    const spinner = document.getElementById('templateSpinner');
    const select = document.getElementById('templateSelect');

    // A. Populate Inputs (Editable)
    const companyName = currentUserData.company || currentUserData.name || "";
    document.getElementById('edit_company').value = companyName;
    document.getElementById('edit_email').value = currentUserData.email || "";
    document.getElementById('edit_phone').value = currentUserData.mobile || currentUserData.phone || "";
    
    // B. Auto-set Project Name
    document.getElementById('edit_project_name').value = companyName ? `${companyName}'s Project` : "New Project";

    // C. Reset Fields
    document.getElementById('edit_notes').value = "";
    document.getElementById('newCustomTask').value = "";
    currentCustomTasks = [];
    renderCustomTasks();
    document.getElementById('templateTasksContainer').innerHTML = '<p class="text-gray-400 text-sm italic text-center mt-10">Loading templates...</p>';

    modal.classList.remove('hidden');
    spinner.classList.remove('hidden');

    // D. Fetch Templates from Backend
    try {
        const response = await fetch('/api/service-templates');
        if (!response.ok) throw new Error('Failed to load templates');
        availableTemplates = await response.json();

        // Populate Select Dropdown
        select.innerHTML = '<option value="">-- Select Service Template --</option>';
        availableTemplates.forEach(tpl => {
            const option = document.createElement('option');
            option.value = tpl.id;
            option.text = tpl.name;
            select.appendChild(option);
        });

        document.getElementById('templateTasksContainer').innerHTML = '<p class="text-gray-400 text-sm italic text-center mt-10">Please select a template.</p>';

    } catch (error) {
        document.getElementById('templateTasksContainer').innerHTML = '<p class="text-red-500 text-sm text-center">Error loading templates.</p>';
    } finally {
        spinner.classList.add('hidden');
    }
}

function closeProjectModal() {
    document.getElementById('projectModal').classList.add('hidden');
}

// --- 2. Logic to Update Project Name Automatically ---
function autoUpdateProjectName() {
    const comp = document.getElementById('edit_company').value;
    const projInput = document.getElementById('edit_project_name');
    // Simple logic: Update only if the user hasn't completely custom-written something unrelated
    // For now, we just overwrite it for convenience
    if(comp) {
        projInput.value = `${comp}'s Project`;
    }
}

function handleTemplateChange() {
    updateTaskListHeading(false);
    const select = document.getElementById('templateSelect');
    const container = document.getElementById('templateTasksContainer');

    const templateId = parseInt(select.value);

    if (!templateId) {
        container.innerHTML = '<p class="text-gray-400 text-sm italic text-center mt-10">Please select a template.</p>';
        return;
    }

    // Find the template object
    const template = availableTemplates.find(t => t.id === templateId);
    



    if (template && template.default_tasks && template.default_tasks.length > 0) {
        // Sort tasks by sequence number
        const tasks = template.default_tasks.sort((a, b) => (a.sequence_number || 0) - (b.sequence_number || 0));

        // Calculate the midpoint to split tasks into two rows
        const midpoint = Math.ceil(tasks.length / 2);
        const row1Tasks = tasks.slice(0, midpoint);
        const row2Tasks = tasks.slice(midpoint);

        // Function to generate the HTML for a row of tasks
        const renderTasks = (taskList) => {
            return taskList.map(task => `
                <label class="flex-shrink-0 w-64 p-3 border border-gray-200 rounded-xl cursor-pointer transition 
                            hover:bg-gray-50 hover:border-gray-300">
                    <div class="flex items-start">
                        <div class="flex items-center h-5">
                            <input type="checkbox" name="template_task" value="${task.id}" checked 
                                class="h-4 w-4 accent-gray-800 text-gray-800 focus:ring-gray-800 border-gray-300 rounded-md">
                        </div>
                        
                        <div class="ml-3 text-sm">
                            <span class="font-semibold text-gray-800">${task.title}</span>
                            ${task.description ? `<p class="text-gray-500 text-xs mt-0.5">${task.description}</p>` : ''}
                        </div>
                    </div>
                </label>
            `).join('');
        };

        // Render Tasks in a two-row structure. Both rows are horizontally flexible.
        container.innerHTML = `
            <div class="space-y-4 pb-1"> <div class="flex space-x-4"> 
                    ${renderTasks(row1Tasks)}
                </div>

                ${row2Tasks.length > 0 ? `
                    <div class="flex space-x-4">
                        ${renderTasks(row2Tasks)}
                    </div>
                ` : ''}
            </div>
        `;
    } else {
        container.innerHTML = '<p class="text-gray-500 text-sm text-center py-4">No default tasks defined for this template.</p>';
    }
}

// --- 4. Custom Task Logic ---
function addCustomTask() {
    const input = document.getElementById('newCustomTask');
    const val = input.value.trim();
    if (val) {
        currentCustomTasks.push(val);
        input.value = '';
        renderCustomTasks();
    }
}

function renderCustomTasks() {
    const list = document.getElementById('customTasksList');
    list.innerHTML = currentCustomTasks.map((t, i) => `
        <li class="bg-indigo-100 text-indigo-800 px-2 py-1 rounded text-xs flex items-center gap-2">
            ${t}
            <button onclick="removeCustomTask(${i})" class="hover:text-red-600 font-bold">&times;</button>
        </li>
    `).join('');
}

function removeCustomTask(i) {
    currentCustomTasks.splice(i, 1);
    renderCustomTasks();
}

async function submitProjectCreation() {
    const btn = document.getElementById('submitProjectBtn');

    const company = document.getElementById('edit_company').value.trim();
    const projectName = document.getElementById('edit_project_name').value.trim();

    if (!company || !projectName) {
        showPopup("Company Name and Project Name are required.");
        return;
    }

    btn.innerText = "Processing...";
    btn.disabled = true;

    try {
        const checkedBoxes = document.querySelectorAll('input[name="template_task"]:checked');
        const taskIds = Array.from(checkedBoxes).map(cb => parseInt(cb.value));

        const aiTasksSelected = Array.from(document.querySelectorAll('input[name="ai_generated_task"]:checked'))
            .map(i => {
                const raw = i.value || "";
                try {
                    return JSON.parse(decodeURIComponent(raw));
                } catch (err) {
                    const [title = "", details = ""] = raw.split('||');
                    return { title: title || '', description: details || '' };
                }
            })
            .filter(t => t && (t.title || t.description)); // drop empty items

        const payload = {
            company_name: company,
            email: document.getElementById('edit_email').value.trim(),
            phone: document.getElementById('edit_phone').value.trim(),
            project_name: projectName,
            notes: document.getElementById('edit_notes').value,
            template_id: document.getElementById('templateSelect').value ? parseInt(document.getElementById('templateSelect').value) : null,
            selected_task_ids: taskIds,
            custom_tasks: currentCustomTasks || []
        };
        if (aiTasksSelected.length > 0) {
            payload.ai_tasks = aiTasksSelected;
        }

        const response = await fetch(`/api/sessions/${currentSessionId}/project`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const result = await response.json();

        if (response.ok) {
            showPopup("Project Created Successfully!");
            closeProjectModal();
        } else {
            showPopup(result.detail || "Error Creating Project");
        }

    } catch (error) {
        console.error("submitProjectCreation error:", error);
        showPopup("Network Error");
    } finally {
        btn.innerText = "Create Project & Save Changes";
        btn.disabled = false;
    }
}



// --- AI Task Generation (Frontend) ---
// Adds a new AI suggestions block to the templateTasksContainer
async function generateAutoTasks() {
    const btn = document.getElementById('generateAiTasksBtn');
    
    const label = document.getElementById('generateAiBtnLabel');
    const container = document.getElementById('templateTasksContainer');
    const select = document.getElementById('templateSelect');

    // Prevent double clicks
    if (btn.dataset.busy === "1") return;
    btn.dataset.busy = "1";
    document.getElementById('magicSpark').classList.add('spin-magic');
    label.textContent = 'Generating...';


    try {
        // Build payload to backend. Send session id and optionally selected template id and some UI fields
        const payload = {
            session_id: window.currentSessionId || null,
            template_id: select.value ? parseInt(select.value) : null,
            project_name: document.getElementById('edit_project_name').value || null,
            company: document.getElementById('edit_company').value || null,
            email: document.getElementById('edit_email').value || null,
            phone: document.getElementById('edit_phone').value || null,
            notes: document.getElementById('edit_notes').value || null
        };

        const resp = await fetch('/api/projects/generate-tasks', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });

        if (!resp.ok) {
            const text = await resp.text();
            throw new Error(text || 'Failed to generate tasks');
        }

        const data = await resp.json();
        const tasks = data.tasks || [];

        // Render AI suggestions in the container (create or replace AI section)
        renderAITaskSuggestions(tasks);

    } catch (err) {
        console.error('AI generation error', err);
        // Show an inline error message inside templateTasksContainer (non-destructive)
        const errNode = document.createElement('p');
        errNode.className = 'text-red-500 text-sm text-center';
        errNode.textContent = 'AI task generation failed. Please try again.';
        // place at top
        container.prepend(errNode);
        setTimeout(() => errNode.remove(), 6000);
    } finally {
        document.getElementById('magicSpark').classList.remove('spin-magic');
        label.textContent = 'Auto-generate tasks';
        btn.dataset.busy = "0";
    }
}

// Helper to render AI task suggestions (checkbox list) above the current tasks area.
function renderAITaskSuggestions(taskList) {
    const container = document.getElementById('templateTasksContainer');
    updateTaskListHeading(taskList.length > 0);

    // Remove existing AI section if present
    const existing = document.getElementById('aiSuggestionsSection');
    if (existing) existing.remove();

    const rowsHtml = taskList.map((task, idx) => {
        // Use ai_generated_task as name so existing template_task processing won't be impacted.
        const id = `ai_task_${idx}`;
        return `
            <label class="flex-shrink-0 w-64 p-3 border border-gray-200 rounded-xl cursor-pointer transition hover:bg-gray-50 hover:border-gray-300">
                <div class="flex items-start">
                    <div class="flex items-center h-5">
                        <input type="checkbox" name="ai_generated_task" value="${escapeHtml(task.title)}||${escapeHtml(task.description || '')}" checked
                            class="h-4 w-4 accent-gray-800 focus:ring-gray-800 border-gray-300 rounded-md">
                    </div>
                    <div class="ml-3 text-sm">
                        <span class="font-semibold text-gray-800">${escapeHtml(task.title)}</span>
                        ${task.description ? `<p class="text-gray-500 text-xs mt-0.5">${escapeHtml(task.description)}</p>` : ''}
                    </div>
                </div>
            </label>
        `;
    }).join('');

    const section = document.createElement('div');
    section.id = 'aiSuggestionsSection';
    section.className = 'space-y-4 pb-1';
    section.innerHTML = `
        <div class="mb-2">
            <div class="flex gap-3">
                ${rowsHtml || '<p class="text-gray-500 text-sm italic">No suggestions returned.</p>'}
            </div>
        </div>
    `;
    container.prepend(section);
}

function updateTaskListHeading(hasAI) {
    const taskHead = document.getElementById("task-head");
    if (!taskHead) return;

    if (hasAI) {
        taskHead.innerHTML = `TASK LIST <span class="text-blue-600 font-semibold">(AI Suggestions Added)</span>`;
    } else {
        taskHead.textContent = "TASK LIST";
    }
}

function closeChat() {
    reconnectAttempts = maxReconnectAttempts;
    if (currentWs) {
    currentWs.close();
    }
    currentWs = null;
    currentWsUrl = null;
    currentSessionId = null;
    currentMode = null;
    currentUserData = {};
    const modal = document.getElementById('chatModal');
    modal.classList.add('hidden');
    document.getElementById('messagesContainer').innerHTML = '';
}
function connectWebSocket() {
    if (!currentWsUrl) return;
    try {
    currentWs = new WebSocket(currentWsUrl);
    currentWs.onopen = () => {
        reconnectAttempts = 0;
    };
    currentWs.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'history') {
        renderMessages(data.messages || []);
        } else if (data.type === 'message') {
        if (data.role === 'admin' && data.content === lastSentContent) {
            return;
        }
        addMessage(data);
        } else if (data.type === 'handover') {
        addMessage({ role: 'system', content: data.content, timestamp: new Date().toISOString() });
        }
    };
    currentWs.onclose = () => {
        currentWs = null;
        if (reconnectAttempts < maxReconnectAttempts) {
        setTimeout(() => {
            reconnectAttempts++;
            connectWebSocket();
        }, 1000 * reconnectAttempts);
        }
    };
    currentWs.onerror = (error) => {
        console.error('WebSocket error:');
    };
    } catch (err) {
    console.error('Failed to create WebSocket');
    }
}
function renderMessages(messages) {
    const container = document.getElementById('messagesContainer');
    container.innerHTML = '';
    messages.forEach(msg => {
    container.insertAdjacentHTML('beforeend', renderMessageHtml(msg));
    const last = container.lastElementChild;
    requestAnimationFrame(() => last.classList.add('in'));
    });
    container.scrollTop = container.scrollHeight;
}
function renderMessageHtml(msg) {
    const ts = msg.timestamp ? new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
    const role = (msg.role || '').toLowerCase();

    if (role === 'system') {
    return `
        <div class="msg-animate in flex justify-center">
        <div class="text-xs px-4 py-2 bg-gray-100 rounded-full border border-gray-200 text-gray-500">
            ${escapeHtml(msg.content)}
        </div>
        </div>
    `;
    }

    if (role === 'user') {
    return `
        <div class="msg-animate in flex ">
        <div class="max-w-[80%]">
            <div class="flex items-center gap-2 mb-1">
            <span class="text-xs text-gray-400">${ts}</span>
            <span class="text-xs font-medium text-gray-900">${escapeHtml(msg.name || 'User')}</span>
            </div>
            <div class="p-3 bg-gray-900 text-white rounded-2xl shadow-sm text-sm max-w-md">
            ${escapeHtml(msg.content)}
            </div>
        </div>
        </div>
    `;
    }

    if (role === 'bot') {
    let html = '';
    if (msg.mood || msg.interest) {
        html += `
            <div class="flex flex-wrap gap-2 text-xs text-gray-500">
                ${msg.mood ? `<span class="inline-flex items-center gap-1 px-2 py-1 bg-gray-100 rounded-full" title="detected mood of user from previous message">
                <svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3" viewBox="0 0 20 20" fill="currentColor">
                    <path d="M10 18a8 8 0 100-16 8 8 0 000 16zm-3-7a1 1 0 112 0h2a1 1 0 112 0 4 4 0 01-6 0zM7 8a1 1 0 110-2 1 1 0 010 2zm6 0a1 1 0 110-2 1 1 0 010 2z" />
                </svg>
                ${msg.mood}
                </span>` : ''}
                ${msg.interest ? `<span class="inline-flex items-center gap-1 px-2 py-1 bg-gray-100 rounded-full" title="detected interest of user from previous message">
                <svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3" viewBox="0 0 20 20" fill="currentColor">
                    <path d="M2 10a8 8 0 1116 0A8 8 0 012 10zm8 4a4 4 0 100-8 4 4 0 000 8z" />
                </svg>
                ${msg.interest}
                </span>` : ''}
            </div>
        `;
    }
    html += `
        <div class="msg-animate in flex justify-end">
        <div class="max-w-[80%]">
            <div class="flex items-center justify-end gap-2 mb-1">
            <span class="text-xs font-medium text-gray-900">AI</span>
            <span class="text-xs text-gray-400">${ts}</span>
            </div>
            <div class="p-3 bg-gray-50 border border-gray-200 rounded-2xl text-sm max-w-md">
            ${escapeHtml(msg.content)}
            </div>
        </div>
        </div>
    `;
    return html;
    }

    if (role === 'admin') {
    return `
        <div class="msg-animate in flex justify-end">
        <div class="max-w-[80%]">
            <div class="flex items-center justify-end gap-2 mb-1">
            <span class="text-xs font-medium text-gray-900">Admin</span>
            <span class="text-xs text-gray-400">${ts}</span>
            </div>
            <div class="p-3 bg-gray-50 border border-gray-200 rounded-2xl text-sm max-w-md">
            ${escapeHtml(msg.content)}
            </div>
        </div>
        </div>
    `;
    }
}
function addMessage(msg) {
    const container = document.getElementById('messagesContainer');
    const normalized = {
    role: msg.role || msg.from || 'assistant',
    content: msg.content || msg.message || '',
    timestamp: msg.timestamp || new Date().toISOString(),
    name: msg.name || msg.sender_name || '',
    mood: msg.mood,
    interest: msg.interest
    };
    container.insertAdjacentHTML('beforeend', renderMessageHtml(normalized));
    const last = container.lastElementChild;
    requestAnimationFrame(() => last.classList.add('in'));
    container.scrollTop = container.scrollHeight;
}
function sendMessage() {
    if (currentMode !== 'control') return;
    const input = document.getElementById('messageInput');
    const content = input.value.trim();
    if (content && currentWs) {
    lastSentContent = content;
    currentWs.send(JSON.stringify({ type: 'message', content }));
    input.value = '';
    addMessage({ role: 'admin', content: content, timestamp: new Date().toISOString(), name: 'Admin' });
    setTimeout(() => { lastSentContent = null; }, 1000);
    }
}
function handover() {
    if (currentMode !== 'control' || !currentWs) return;
    document.getElementById('inputArea').style.display = 'none';
    document.getElementById('ModeTrack').innerText = 'View Mode';
    currentWs.send(JSON.stringify({ type: 'handover' }));
}
function closeWarningModal() {
    document.getElementById('warningModal').style.display = 'none';
}
function showWarning(message) {
    return new Promise((resolve, reject) => {
    document.getElementById('warningMessage').textContent = message;
    document.getElementById('warningModal').style.display = 'flex';
    const cancelBtn = document.getElementById('cancelBtn');
    const proceedBtn = document.getElementById('proceedBtn');
    const closeAndCleanup = () => {
        document.getElementById('warningModal').style.display = 'none';
        cancelBtn.onclick = null;
        proceedBtn.onclick = null;
    };
    cancelBtn.onclick = () => { closeAndCleanup(); reject(new Error('cancelled')); };
    proceedBtn.onclick = () => { closeAndCleanup(); resolve(); };
    });
}
function escapeHtml(str) {
    if (str == null) return '';
    return String(str).replace(/[&<>"'`]/g, (m) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;', '`':'&#96;'}[m]));
}
document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('allSessionsBtn').addEventListener('click', toggleActive);
    document.getElementById('activeOnlyBtn').addEventListener('click', toggleActive);
    document.getElementById('sendBtn').addEventListener('click', sendMessage);
    document.getElementById('messageInput').addEventListener('keypress', (e) => {
    if (e.key === 'Enter') sendMessage();
    });
    document.getElementById('handoverBtn').addEventListener('click', handover);
    document.getElementById('closeModalBtn').addEventListener('click', closeChat);
    loadSessions();
    startSessionAutoRefresh(5000);
});
function startSessionAutoRefresh(intervalMs = 10000) {
    if (sessionRefreshInterval) clearInterval(sessionRefreshInterval);
    sessionRefreshInterval = setInterval(() => {
    loadSessions(currentPage);
    }, intervalMs);
}

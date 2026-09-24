/**
 * App Menu Inspector - Frontend Application Logic
 */

let allApps = [];
let appStats = {};
let currentSourceFilter = "all";
let currentCategoryFilter = "all";
let currentSort = "name_asc";
let currentSearchTerm = "";
let currentViewMode = "table"; // "cards" or "table"

// DOM Elements
const loadingSpinner = document.getElementById("loading-spinner");
const emptyState = document.getElementById("empty-state");
const appsGrid = document.getElementById("apps-grid");
const appsTableContainer = document.getElementById("apps-table-container");
const appsTableBody = document.getElementById("apps-table-body");
const searchInput = document.getElementById("search-input");
const clearSearchBtn = document.getElementById("clear-search");
const categoryFilter = document.getElementById("category-filter");
const sortBy = document.getElementById("sort-by");
const viewCardsBtn = document.getElementById("view-cards-btn");
const viewTableBtn = document.getElementById("view-table-btn");
const toggleHidden = document.getElementById("toggle-hidden");
const btnRefresh = document.getElementById("btn-refresh");
const btnExport = document.getElementById("btn-export");
const exportMenu = document.getElementById("export-menu");
const toast = document.getElementById("toast");

// Modal Elements
const detailModal = document.getElementById("detail-modal");
const modalCloseBtn = document.getElementById("modal-close-btn");
const modalAppIcon = document.getElementById("modal-app-icon");
const modalAppName = document.getElementById("modal-app-name");
const modalAppGeneric = document.getElementById("modal-app-generic");
const modalSourceLabel = document.getElementById("modal-source-label");
const modalPackageName = document.getElementById("modal-package-name");
const modalVersionArch = document.getElementById("modal-version-arch");
const modalSize = document.getElementById("modal-size");
const modalMenuStatus = document.getElementById("modal-menu-status");
const modalCategory = document.getElementById("modal-category");
const modalExec = document.getElementById("modal-exec");
const modalDesktopPath = document.getElementById("modal-desktop-path");
const modalUninstallCmd = document.getElementById("modal-uninstall-cmd");
const modalUninstallNote = document.getElementById("modal-uninstall-note");
const modalCopyCmdBtn = document.getElementById("modal-copy-cmd-btn");
const btnToggleRaw = document.getElementById("btn-toggle-raw");
const rawDesktopContent = document.getElementById("raw-desktop-content");

let activeModalApp = null;

// Initialize on page load
document.addEventListener("DOMContentLoaded", () => {
  setupEventListeners();
  loadApplications();
});

function setupEventListeners() {
  // Search input
  searchInput.addEventListener("input", (e) => {
    currentSearchTerm = e.target.value.trim().toLowerCase();
    clearSearchBtn.style.display = currentSearchTerm ? "block" : "none";
    renderApplications();
  });

  clearSearchBtn.addEventListener("click", () => {
    searchInput.value = "";
    currentSearchTerm = "";
    clearSearchBtn.style.display = "none";
    searchInput.focus();
    renderApplications();
  });

  // Category filter
  categoryFilter.addEventListener("change", (e) => {
    currentCategoryFilter = e.target.value;
    renderApplications();
  });

  // Sort dropdown
  sortBy.addEventListener("change", (e) => {
    currentSort = e.target.value;
    renderApplications();
  });

  // Toggle Hidden Apps
  if (toggleHidden) {
    toggleHidden.addEventListener("change", () => {
      renderApplications();
    });
  }

  // View toggle
  viewCardsBtn.addEventListener("click", () => {
    currentViewMode = "cards";
    viewCardsBtn.classList.add("active");
    viewTableBtn.classList.remove("active");
    appsGrid.style.display = "grid";
    appsTableContainer.style.display = "none";
    renderApplications();
  });

  viewTableBtn.addEventListener("click", () => {
    currentViewMode = "table";
    viewTableBtn.classList.add("active");
    viewCardsBtn.classList.remove("active");
    appsGrid.style.display = "none";
    appsTableContainer.style.display = "block";
    renderApplications();
  });

  // Filter tabs
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentSourceFilter = btn.dataset.source;
      renderApplications();
    });
  });

  // Stat pills and cards clicking
  document.querySelectorAll(".stat-pill, .stat-card").forEach((card) => {
    card.addEventListener("click", () => {
      const filter = card.dataset.filter;
      const targetTab = document.querySelector(`.tab-btn[data-source="${filter}"]`);
      if (targetTab) {
        targetTab.click();
      }
    });
  });

  // Refresh button
  btnRefresh.addEventListener("click", async () => {
    btnRefresh.classList.add("loading");
    showToast("Rescanning system packages...");
    try {
      const res = await fetch("/api/refresh", { method: "POST" });
      const data = await res.json();
      allApps = data.applications || [];
      appStats = data.stats || {};
      updateStatsUI();
      renderApplications();
      showToast("Scan complete: " + allApps.length + " applications found");
    } catch (err) {
      showToast("Failed to rescan applications");
    } finally {
      btnRefresh.classList.remove("loading");
    }
  });

  // Export dropdown
  btnExport.addEventListener("click", (e) => {
    e.stopPropagation();
    btnExport.parentElement.classList.toggle("open");
  });

  document.addEventListener("click", () => {
    document.querySelectorAll(".dropdown").forEach((d) => d.classList.remove("open"));
  });

  // Reset filters button
  document.getElementById("btn-reset-filters").addEventListener("click", () => {
    searchInput.value = "";
    currentSearchTerm = "";
    clearSearchBtn.style.display = "none";
    categoryFilter.value = "all";
    currentCategoryFilter = "all";
    const allTab = document.querySelector('.tab-btn[data-source="all"]');
    if (allTab) allTab.click();
  });

  // Modal close
  modalCloseBtn.addEventListener("click", closeModal);
  detailModal.addEventListener("click", (e) => {
    if (e.target === detailModal) closeModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && detailModal.style.display !== "none") closeModal();
  });

  // Modal Copy Command
  modalCopyCmdBtn.addEventListener("click", () => {
    if (activeModalApp && activeModalApp.uninstall_command) {
      copyToClipboard(activeModalApp.uninstall_command, "Uninstall command copied");
    }
  });

  // Modal toggle raw desktop content
  btnToggleRaw.addEventListener("click", async () => {
    if (rawDesktopContent.style.display === "none") {
      if (!rawDesktopContent.textContent && activeModalApp && activeModalApp.desktop_path) {
        try {
          const res = await fetch(`/api/desktop-content?path=${encodeURIComponent(activeModalApp.desktop_path)}`);
          if (res.ok) {
            const data = await res.json();
            rawDesktopContent.textContent = data.content;
          } else {
            rawDesktopContent.textContent = "Unable to read desktop file.";
          }
        } catch (err) {
          rawDesktopContent.textContent = "Error reading desktop file.";
        }
      }
      rawDesktopContent.style.display = "block";
      btnToggleRaw.textContent = "Hide File Contents";
    } else {
      rawDesktopContent.style.display = "none";
      btnToggleRaw.textContent = "View File Contents";
    }
  });

  // Modal small copy buttons
  document.querySelectorAll(".copy-small-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const targetId = btn.dataset.target;
      const targetElem = document.getElementById(targetId);
      if (targetElem) {
        copyToClipboard(targetElem.textContent, "Copied to clipboard");
      }
    });
  });
}

// Fetch applications data
async function loadApplications() {
  loadingSpinner.style.display = "flex";
  appsGrid.style.display = "none";
  appsTableContainer.style.display = "none";

  try {
    const res = await fetch("/api/apps");
    const data = await res.json();
    allApps = data.applications || [];
    appStats = data.stats || {};
    updateStatsUI();
    renderApplications();
  } catch (err) {
    showToast("Error loading application catalog");
  } finally {
    loadingSpinner.style.display = "none";
  }
}

// Update counters in cards and tabs
function updateStatsUI() {
  document.getElementById("stat-total").textContent = appStats.total ?? allApps.length;
  document.getElementById("stat-menu").textContent = appStats.in_menu ?? 0;
  document.getElementById("stat-rpm").textContent = appStats.repo_rpm ?? 0;
  document.getElementById("stat-flatpak").textContent = appStats.flatpak ?? 0;
  document.getElementById("stat-local").textContent = appStats.local_tool ?? 0;
  document.getElementById("stat-appimage").textContent = appStats.appimage ?? 0;

  // Tabs count badges
  document.getElementById("count-all").textContent = appStats.total ?? allApps.length;
  document.getElementById("count-menu").textContent = appStats.in_menu ?? 0;
  document.getElementById("count-local").textContent = appStats.local_tool ?? 0;
  document.getElementById("count-rpm").textContent = appStats.repo_rpm ?? 0;
  document.getElementById("count-flatpak").textContent = appStats.flatpak ?? 0;
  document.getElementById("count-appimage").textContent = appStats.appimage ?? 0;
  document.getElementById("count-steam").textContent = appStats.steam ?? 0;
  document.getElementById("count-unmanaged").textContent = appStats.unmanaged ?? 0;
  document.getElementById("count-hidden").textContent = appStats.hidden ?? 0;
}

// Filter and sort applications
function getFilteredApps() {
  const showHidden = toggleHidden ? toggleHidden.checked : false;

  return allApps.filter((app) => {
    // Source / Menu Filter
    if (currentSourceFilter === "hidden") {
      if (app.in_menu) return false;
    } else if (currentSourceFilter === "in_menu") {
      if (!app.in_menu) return false;
    } else {
      if (currentSourceFilter !== "all" && app.source_type !== currentSourceFilter) {
        return false;
      }
      if (!showHidden && !app.in_menu) {
        return false;
      }
    }

    // Category Filter
    if (currentCategoryFilter !== "all") {
      if (app.primary_category !== currentCategoryFilter) return false;
    }

    // Text Search Filter
    if (currentSearchTerm) {
      const match =
        (app.name && app.name.toLowerCase().includes(currentSearchTerm)) ||
        (app.generic_name && app.generic_name.toLowerCase().includes(currentSearchTerm)) ||
        (app.comment && app.comment.toLowerCase().includes(currentSearchTerm)) ||
        (app.package_name && app.package_name.toLowerCase().includes(currentSearchTerm)) ||
        (app.exec_command && app.exec_command.toLowerCase().includes(currentSearchTerm)) ||
        (app.desktop_filename && app.desktop_filename.toLowerCase().includes(currentSearchTerm));
      if (!match) return false;
    }

    return true;
  }).sort((a, b) => {
    if (currentSort === "name_asc") {
      return a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
    } else if (currentSort === "name_desc") {
      return b.name.localeCompare(a.name, undefined, { sensitivity: "base" });
    } else if (currentSort === "source") {
      return a.source_label.localeCompare(b.source_label);
    } else if (currentSort === "pkg") {
      return (a.package_name || "").localeCompare(b.package_name || "");
    }
    return 0;
  });
}

// Render the application list
function renderApplications() {
  const filtered = getFilteredApps();

  if (filtered.length === 0) {
    emptyState.style.display = "flex";
    appsGrid.style.display = "none";
    appsTableContainer.style.display = "none";
    return;
  }

  emptyState.style.display = "none";

  if (currentViewMode === "cards") {
    appsGrid.style.display = "grid";
    appsTableContainer.style.display = "none";
    renderCards(filtered);
  } else {
    appsGrid.style.display = "none";
    appsTableContainer.style.display = "block";
    renderTable(filtered);
  }
}

// Render cards
function renderCards(apps) {
  appsGrid.innerHTML = "";
  const fragment = document.createDocumentFragment();

  apps.forEach((app) => {
    const card = document.createElement("div");
    card.className = "app-card";

    const iconUrl = app.icon
      ? `/api/icon?name=${encodeURIComponent(app.icon)}`
      : "/api/icon";

    const badgeClass = `badge-source-${app.source_type}`;
    const menuBadgeClass = app.in_menu ? "badge-menu" : "badge-hidden";
    const menuBadgeText = app.in_menu ? "In Menu" : "Hidden";

    const versionSizeStr = [app.package_version, app.installed_size].filter(Boolean).join(" • ");

    card.innerHTML = `
      <div>
        <div class="card-top">
          <img class="app-icon" src="${iconUrl}" alt="" loading="lazy" onerror="this.src='/api/icon'">
          <div class="card-header-info">
            <div class="card-title-row">
              <h3 class="app-name" title="${escapeHtml(app.name)}">${escapeHtml(app.name)}</h3>
            </div>
            ${app.generic_name ? `<div class="app-generic-name">${escapeHtml(app.generic_name)}</div>` : ""}
            <div class="card-badges">
              <span class="badge ${badgeClass}">${escapeHtml(app.source_label)}</span>
              <span class="badge ${menuBadgeClass}">${menuBadgeText}</span>
              ${app.primary_category ? `<span class="badge badge-unmanaged">${escapeHtml(app.primary_category)}</span>` : ""}
            </div>
          </div>
        </div>

        <p class="app-description" title="${escapeHtml(app.comment || "No description available")}">
          ${escapeHtml(app.comment || "No description available")}
        </p>
      </div>

      <div>
        <div class="card-package-row">
          <div class="pkg-label-group">
            <span class="pkg-tag">Pkg:</span>
            <span class="pkg-name-code" title="${escapeHtml(app.package_name || "")}">
              ${escapeHtml(app.package_name || "N/A")}
            </span>
          </div>
          ${app.package_name ? `
            <button class="copy-icon-btn btn-copy-pkg" title="Copy package name">
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
              </svg>
            </button>
          ` : ""}
        </div>

        ${app.uninstall_command ? `
          <div class="card-uninstall-block" style="margin-top: 8px;">
            <code class="uninstall-code" title="${escapeHtml(app.uninstall_command)}">${escapeHtml(app.uninstall_command)}</code>
            <button class="copy-cmd-btn btn-copy-cmd">Copy</button>
          </div>
        ` : ""}

        <div class="card-footer">
          <span class="meta-size-ver">${escapeHtml(versionSizeStr)}</span>
          <button class="btn btn-outline btn-sm btn-inspect">Inspect</button>
        </div>
      </div>
    `;

    // Event listeners on card buttons
    const btnCopyPkg = card.querySelector(".btn-copy-pkg");
    if (btnCopyPkg) {
      btnCopyPkg.addEventListener("click", (e) => {
        e.stopPropagation();
        copyToClipboard(app.package_name, "Package name copied: " + app.package_name);
      });
    }

    const btnCopyCmd = card.querySelector(".btn-copy-cmd");
    if (btnCopyCmd) {
      btnCopyCmd.addEventListener("click", (e) => {
        e.stopPropagation();
        copyToClipboard(app.uninstall_command, "Uninstall command copied");
      });
    }

    const btnInspect = card.querySelector(".btn-inspect");
    if (btnInspect) {
      btnInspect.addEventListener("click", () => openModal(app));
    }

    fragment.appendChild(card);
  });

  appsGrid.appendChild(fragment);
}

// Render table view
function renderTable(apps) {
  appsTableBody.innerHTML = "";
  const fragment = document.createDocumentFragment();

  apps.forEach((app) => {
    const tr = document.createElement("tr");

    const iconUrl = app.icon
      ? `/api/icon?name=${encodeURIComponent(app.icon)}`
      : "/api/icon";

    const badgeClass = `badge-source-${app.source_type}`;

    tr.innerHTML = `
      <td>
        <img class="table-icon" src="${iconUrl}" alt="" loading="lazy" onerror="this.src='/api/icon'">
      </td>
      <td>
        <strong class="table-app-name">${escapeHtml(app.name)}</strong>
        ${app.generic_name ? `<div class="table-app-generic">${escapeHtml(app.generic_name)}</div>` : ""}
      </td>
      <td>
        <span class="badge ${badgeClass}">${escapeHtml(app.source_label)}</span>
      </td>
      <td>
        <div class="table-pkg-cell">
          <code class="table-pkg-code" title="${escapeHtml(app.package_name || "")}">
            ${escapeHtml(app.package_name || "N/A")}
          </code>
          ${app.package_name ? `
            <button class="copy-icon-btn btn-table-copy-pkg" title="Copy package name">
              <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
              </svg>
            </button>
          ` : ""}
        </div>
      </td>
      <td>
        ${app.uninstall_command ? `
          <div class="table-cmd-cell">
            <code class="table-cmd-code" title="${escapeHtml(app.uninstall_command)}">${escapeHtml(app.uninstall_command)}</code>
            <button class="copy-cmd-btn btn-table-copy" title="Copy uninstall command">Copy</button>
          </div>
        ` : `<span style="color: var(--text-muted);">-</span>`}
      </td>
      <td>
        <span class="badge ${app.in_menu ? "badge-menu" : "badge-hidden"}">
          ${app.in_menu ? "In Menu" : "Hidden"}
        </span>
      </td>
      <td>
        <button class="btn btn-outline btn-sm btn-table-inspect">Inspect</button>
      </td>
    `;

    const btnTableCopyPkg = tr.querySelector(".btn-table-copy-pkg");
    if (btnTableCopyPkg) {
      btnTableCopyPkg.addEventListener("click", () => {
        copyToClipboard(app.package_name, "Package name copied: " + app.package_name);
      });
    }

    const btnTableCopy = tr.querySelector(".btn-table-copy");
    if (btnTableCopy) {
      btnTableCopy.addEventListener("click", () => {
        copyToClipboard(app.uninstall_command, "Uninstall command copied");
      });
    }

    const btnTableInspect = tr.querySelector(".btn-table-inspect");
    if (btnTableInspect) {
      btnTableInspect.addEventListener("click", () => openModal(app));
    }

    fragment.appendChild(tr);
  });

  appsTableBody.appendChild(fragment);
}

// Open Detail Modal
function openModal(app) {
  activeModalApp = app;

  const iconUrl = app.icon
    ? `/api/icon?name=${encodeURIComponent(app.icon)}`
    : "/api/icon";

  modalAppIcon.src = iconUrl;
  modalAppName.textContent = app.name;
  modalAppGeneric.textContent = app.generic_name || app.comment || "";
  modalSourceLabel.textContent = app.source_label;
  modalPackageName.textContent = app.package_name || "N/A";
  modalVersionArch.textContent = [app.package_version, app.package_arch].filter(Boolean).join(" / ") || "N/A";
  modalSize.textContent = app.installed_size || "Unknown";
  modalMenuStatus.textContent = app.in_menu ? "Visible in Application Menu" : "Hidden / Helper Application";
  modalCategory.textContent = app.primary_category + (app.categories && app.categories.length ? ` (${app.categories.join(", ")})` : "");
  modalExec.textContent = app.exec_command || "N/A";
  modalDesktopPath.textContent = app.desktop_path || "Standalone executable (no .desktop file)";
  modalUninstallCmd.textContent = app.uninstall_command || "No uninstall command available";
  modalUninstallNote.textContent = app.uninstall_note || "";

  // Reset raw file view
  rawDesktopContent.textContent = "";
  rawDesktopContent.style.display = "none";
  btnToggleRaw.textContent = "View File Contents";

  const rawSection = document.getElementById("modal-raw-container");
  if (!app.desktop_path) {
    rawSection.style.display = "none";
  } else {
    rawSection.style.display = "block";
  }

  detailModal.style.display = "flex";
}

function closeModal() {
  detailModal.style.display = "none";
  activeModalApp = null;
}

// Toast notification helper
let toastTimeout = null;
function showToast(message) {
  toast.textContent = message;
  toast.style.display = "block";
  if (toastTimeout) clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => {
    toast.style.display = "none";
  }, 2800);
}

// Copy to clipboard helper
function copyToClipboard(text, successMsg) {
  if (!text) return;
  navigator.clipboard.writeText(text).then(
    () => {
      showToast(successMsg || "Copied to clipboard");
    },
    () => {
      // Fallback
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      showToast(successMsg || "Copied to clipboard");
    }
  );
}

// HTML escape helper
function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

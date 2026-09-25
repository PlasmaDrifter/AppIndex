/**
 * AppIndex - Frontend Application Logic
 */

let allApps = [];
let appStats = {};
let currentSourceFilter = "all";
let currentCategoryFilter = "all";
let currentSort = "name_asc";
let currentSearchTerm = "";
let currentViewMode = "table"; // "cards" or "table"
let appUpdateData = null;

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

// Settings & Theme Presets
const STORAGE_KEY = "appindex_settings_v1";
const LEGACY_STORAGE_KEY = "app_menu_inspector_settings_v1";

const PRESET_THEMES = {
  "catppuccin": {
    name: "Catppuccin Macchiato",
    colors: {
      "--bg-primary": "#181926",
      "--bg-secondary": "#1e2030",
      "--bg-card": "#24273a",
      "--bg-card-hover": "#2b2f46",
      "--border-color": "#363a4f",
      "--text-primary": "#cad3f5",
      "--text-secondary": "#a5adcb",
      "--text-muted": "#8087a2",
      "--accent-blue": "#8aadf4",
      "--accent-purple": "#c6a0f6",
      "--accent-orange": "#f5a97f",
      "--accent-green": "#a6da95",
      "--accent-red": "#ed8796"
    },
    swatches: ["#181926", "#24273a", "#8aadf4", "#c6a0f6"]
  },
  "tokyo-night": {
    name: "Tokyo Night",
    colors: {
      "--bg-primary": "#1a1b26",
      "--bg-secondary": "#16161e",
      "--bg-card": "#24283b",
      "--bg-card-hover": "#2f354f",
      "--border-color": "#292e42",
      "--text-primary": "#c0caf5",
      "--text-secondary": "#9aa5ce",
      "--text-muted": "#565f89",
      "--accent-blue": "#7aa2f7",
      "--accent-purple": "#bb9af7",
      "--accent-orange": "#ff9e64",
      "--accent-green": "#9ece6a",
      "--accent-red": "#f7768e"
    },
    swatches: ["#1a1b26", "#24283b", "#7aa2f7", "#bb9af7"]
  },
  "nord": {
    name: "Nord",
    colors: {
      "--bg-primary": "#2e3440",
      "--bg-secondary": "#242933",
      "--bg-card": "#3b4252",
      "--bg-card-hover": "#434c5e",
      "--border-color": "#4c566a",
      "--text-primary": "#eceff4",
      "--text-secondary": "#e5e9f0",
      "--text-muted": "#81a1c1",
      "--accent-blue": "#88c0d0",
      "--accent-purple": "#b48ead",
      "--accent-orange": "#d08770",
      "--accent-green": "#a3be8c",
      "--accent-red": "#bf616a"
    },
    swatches: ["#2e3440", "#3b4252", "#88c0d0", "#b48ead"]
  },
  "gruvbox": {
    name: "Gruvbox Dark",
    colors: {
      "--bg-primary": "#1d2021",
      "--bg-secondary": "#282828",
      "--bg-card": "#32302f",
      "--bg-card-hover": "#3c3836",
      "--border-color": "#504945",
      "--text-primary": "#ebdbb2",
      "--text-secondary": "#d5c4a1",
      "--text-muted": "#928374",
      "--accent-blue": "#83a598",
      "--accent-purple": "#d3869b",
      "--accent-orange": "#fe8019",
      "--accent-green": "#b8bb26",
      "--accent-red": "#fb4934"
    },
    swatches: ["#1d2021", "#32302f", "#83a598", "#fe8019"]
  },
  "dracula": {
    name: "Dracula",
    colors: {
      "--bg-primary": "#21222c",
      "--bg-secondary": "#282a36",
      "--bg-card": "#343746",
      "--bg-card-hover": "#44475a",
      "--border-color": "#6272a4",
      "--text-primary": "#f8f8f2",
      "--text-secondary": "#e2e2dc",
      "--text-muted": "#9ea8c7",
      "--accent-blue": "#8be9fd",
      "--accent-purple": "#bd93f9",
      "--accent-orange": "#ffb86c",
      "--accent-green": "#50fa7b",
      "--accent-red": "#ff5555"
    },
    swatches: ["#21222c", "#343746", "#8be9fd", "#bd93f9"]
  },
  "cyberpunk": {
    name: "Cyberpunk",
    colors: {
      "--bg-primary": "#0d0e15",
      "--bg-secondary": "#141622",
      "--bg-card": "#1a1d2e",
      "--bg-card-hover": "#262b45",
      "--border-color": "#2f3659",
      "--text-primary": "#e6e6f0",
      "--text-secondary": "#a0a5c0",
      "--text-muted": "#6a7090",
      "--accent-blue": "#00f0ff",
      "--accent-purple": "#ff007f",
      "--accent-orange": "#ffb800",
      "--accent-green": "#05ffa1",
      "--accent-red": "#ff2a5f"
    },
    swatches: ["#0d0e15", "#1a1d2e", "#00f0ff", "#ff007f"]
  },
  "clean-light": {
    name: "Clean Light",
    colors: {
      "--bg-primary": "#f8fafc",
      "--bg-secondary": "#f1f5f9",
      "--bg-card": "#ffffff",
      "--bg-card-hover": "#f1f5f9",
      "--bg-modal": "#ffffff",
      "--bg-code": "#e2e8f0",
      "--btn-primary-text": "#ffffff",
      "--border-color": "#cbd5e1",
      "--text-primary": "#0f172a",
      "--text-secondary": "#334155",
      "--text-muted": "#64748b",
      "--accent-blue": "#2563eb",
      "--accent-purple": "#7c3aed",
      "--accent-orange": "#ea580c",
      "--accent-green": "#16a34a",
      "--accent-red": "#dc2626",
      "--accent-cyan": "#0284c7",
      "--badge-rpm-bg": "rgba(37, 99, 235, 0.12)",
      "--badge-rpm-border": "rgba(37, 99, 235, 0.35)",
      "--badge-rpm-text": "#1d4ed8",
      "--badge-flatpak-bg": "rgba(22, 163, 74, 0.12)",
      "--badge-flatpak-border": "rgba(22, 163, 74, 0.35)",
      "--badge-flatpak-text": "#15803d",
      "--badge-local-bg": "rgba(202, 138, 4, 0.12)",
      "--badge-local-border": "rgba(202, 138, 4, 0.35)",
      "--badge-local-text": "#a16207",
      "--badge-appimage-bg": "rgba(234, 88, 12, 0.12)",
      "--badge-appimage-border": "rgba(234, 88, 12, 0.35)",
      "--badge-appimage-text": "#c2410c",
      "--badge-steam-bg": "rgba(124, 58, 237, 0.12)",
      "--badge-steam-border": "rgba(124, 58, 237, 0.35)",
      "--badge-steam-text": "#6d28d9",
      "--badge-unmanaged-bg": "rgba(100, 116, 139, 0.12)",
      "--badge-unmanaged-border": "rgba(100, 116, 139, 0.35)",
      "--badge-unmanaged-text": "#334155"
    },
    swatches: ["#f8fafc", "#ffffff", "#2563eb", "#7c3aed"]
  }
};

const COLOR_PICKER_MAP = [
  { inputId: "color-bg-primary", hexId: "hex-bg-primary", varName: "--bg-primary" },
  { inputId: "color-bg-secondary", hexId: "hex-bg-secondary", varName: "--bg-secondary" },
  { inputId: "color-bg-card", hexId: "hex-bg-card", varName: "--bg-card" },
  { inputId: "color-border", hexId: "hex-border", varName: "--border-color" },
  { inputId: "color-text-primary", hexId: "hex-text-primary", varName: "--text-primary" },
  { inputId: "color-text-muted", hexId: "hex-text-muted", varName: "--text-muted" },
  { inputId: "color-accent-blue", hexId: "hex-accent-blue", varName: "--accent-blue" },
  { inputId: "color-accent-purple", hexId: "hex-accent-purple", varName: "--accent-purple" },
];

let userSettings = {
  themeId: "catppuccin",
  density: "standard",
  fontScale: 100,
  customColors: null,
  savedCustomThemes: {},
  showServicesLink: false,
  openServicesInSameTab: false,
  servicesDashboardUrl: "http://localhost:5100",
  showGitHubBtn: true,
  checkForUpdates: true
};

// Settings Modal Elements
const settingsModal = document.getElementById("settings-modal");
const btnSettings = document.getElementById("btn-settings");
const settingsCloseBtn = document.getElementById("settings-close-btn");
const settingsDoneBtn = document.getElementById("settings-done-btn");
const densitySelector = document.getElementById("density-selector");
const fontScaleSlider = document.getElementById("font-scale-slider");
const fontScaleValue = document.getElementById("font-scale-value");
const themePresetsGrid = document.getElementById("theme-presets-grid");
const btnResetTheme = document.getElementById("btn-reset-theme");
const customThemeName = document.getElementById("custom-theme-name");
const btnSaveCustomTheme = document.getElementById("btn-save-custom-theme");
const savedCustomThemesSection = document.getElementById("saved-custom-themes-section");
const savedThemesList = document.getElementById("saved-themes-list");

// Immediate startup theme application (prevent flash)
loadSavedSettings();
applyAllActiveSettings();

let activeModalApp = null;

// Initialize on page load
document.addEventListener("DOMContentLoaded", () => {
  try {
    initSettingsUI();
  } catch (err) {
    console.error("Error initializing settings UI:", err);
  }
  try {
    setupEventListeners();
  } catch (err) {
    console.error("Error setting up event listeners:", err);
  }
  try {
    loadApplications();
  } catch (err) {
    console.error("Error loading applications:", err);
  }
  try {
    if (userSettings.checkForUpdates) {
      checkAppUpdatesAsync();
    }
  } catch (err) {
    console.error("Error checking for updates:", err);
  }
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
  document.querySelectorAll(".tab-btn[data-source]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn[data-source]").forEach((b) => b.classList.remove("active"));
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

  // Export controls
  const exportModalElem = document.getElementById("export-modal");
  const exportCloseElem = document.getElementById("export-close-btn");
  const exportCancelElem = document.getElementById("export-cancel-btn");
  const btnOpenExportModal = document.getElementById("btn-open-export-modal");
  const btnDoExport = document.getElementById("btn-do-export");

  if (btnExport) {
    btnExport.addEventListener("click", (e) => {
      e.stopPropagation();
      openExportModal();
    });
  }

  if (btnOpenExportModal) {
    btnOpenExportModal.addEventListener("click", (e) => {
      e.stopPropagation();
      if (btnExport && btnExport.parentElement) {
        btnExport.parentElement.classList.remove("open");
      }
      openExportModal();
    });
  }

  if (exportCloseElem) exportCloseElem.addEventListener("click", closeExportModal);
  if (exportCancelElem) exportCancelElem.addEventListener("click", closeExportModal);
  if (exportModalElem) {
    exportModalElem.addEventListener("click", (e) => {
      if (e.target === exportModalElem) closeExportModal();
    });
  }

  if (btnDoExport) {
    btnDoExport.addEventListener("click", executeCustomExport);
  }

  // Format cards toggle
  document.querySelectorAll('input[name="export-format"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      document.querySelectorAll(".export-format-card").forEach((card) => {
        const r = card.querySelector('input[type="radio"]');
        card.classList.toggle("active", r && r.checked);
      });
      updateExportSummary();
    });
  });

  // Source selection helper buttons
  const btnExportSourcesAll = document.getElementById("btn-export-sources-all");
  const btnExportSourcesNone = document.getElementById("btn-export-sources-none");
  if (btnExportSourcesAll) {
    btnExportSourcesAll.addEventListener("click", () => {
      document.querySelectorAll('input[name="export-source"]').forEach((cb) => (cb.checked = true));
      updateExportSummary();
    });
  }
  if (btnExportSourcesNone) {
    btnExportSourcesNone.addEventListener("click", () => {
      document.querySelectorAll('input[name="export-source"]').forEach((cb) => (cb.checked = false));
      updateExportSummary();
    });
  }

  // Fields selection helper buttons
  const btnExportFieldsDefault = document.getElementById("btn-export-fields-default");
  const btnExportFieldsAll = document.getElementById("btn-export-fields-all");
  const btnExportFieldsNone = document.getElementById("btn-export-fields-none");
  if (btnExportFieldsDefault) {
    btnExportFieldsDefault.addEventListener("click", () => {
      document.querySelectorAll('input[name="export-field"]').forEach((cb) => {
        cb.checked = cb.dataset.default === "true";
      });
      updateExportSummary();
    });
  }
  if (btnExportFieldsAll) {
    btnExportFieldsAll.addEventListener("click", () => {
      document.querySelectorAll('input[name="export-field"]').forEach((cb) => (cb.checked = true));
      updateExportSummary();
    });
  }
  if (btnExportFieldsNone) {
    btnExportFieldsNone.addEventListener("click", () => {
      document.querySelectorAll('input[name="export-field"]').forEach((cb) => (cb.checked = false));
      updateExportSummary();
    });
  }

  // Live change updates for filters and fields
  document.querySelectorAll('input[name="export-source"], input[name="export-visibility"], input[name="export-field"]').forEach((input) => {
    input.addEventListener("change", updateExportSummary);
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
    if (e.key === "Escape") {
      if (detailModal && detailModal.style.display !== "none") closeModal();
      if (settingsModal && settingsModal.style.display !== "none") closeSettingsModal();
      if (exportModalElem && exportModalElem.style.display !== "none") closeExportModal();
    }
  });

  // Settings Modal controls
  const settingsBtnElem = document.getElementById("btn-settings");
  const settingsModalElem = document.getElementById("settings-modal");
  const settingsCloseElem = document.getElementById("settings-close-btn");
  const settingsDoneElem = document.getElementById("settings-done-btn");

  if (settingsBtnElem) {
    settingsBtnElem.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      openSettingsModal();
    });
  }
  if (settingsCloseElem) {
    settingsCloseElem.addEventListener("click", closeSettingsModal);
  }
  if (settingsDoneElem) {
    settingsDoneElem.addEventListener("click", closeSettingsModal);
  }
  if (settingsModalElem) {
    settingsModalElem.addEventListener("click", (e) => {
      if (e.target === settingsModalElem) closeSettingsModal();
    });
  }

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

// Update counters in tabs
function updateStatsUI() {
  const countAll = document.getElementById("count-all");
  if (countAll) countAll.textContent = appStats.total ?? allApps.length;
  const countMenu = document.getElementById("count-menu");
  if (countMenu) countMenu.textContent = appStats.in_menu ?? 0;
  const countLocal = document.getElementById("count-local");
  if (countLocal) countLocal.textContent = appStats.local_tool ?? 0;
  const repoPkgMgr = document.getElementById("repo-pkg-mgr");
  if (repoPkgMgr && appStats.distro && appStats.distro.pkg_manager_label) {
    repoPkgMgr.textContent = appStats.distro.pkg_manager_label;
  }
  const countRpm = document.getElementById("count-rpm");
  if (countRpm) countRpm.textContent = appStats.repo_system ?? appStats.repo_rpm ?? 0;
  const countFlatpak = document.getElementById("count-flatpak");
  if (countFlatpak) countFlatpak.textContent = appStats.flatpak ?? 0;
  const countAppimage = document.getElementById("count-appimage");
  if (countAppimage) countAppimage.textContent = appStats.appimage ?? 0;
  const countSteam = document.getElementById("count-steam");
  if (countSteam) countSteam.textContent = appStats.steam ?? 0;
  const countUnmanaged = document.getElementById("count-unmanaged");
  if (countUnmanaged) countUnmanaged.textContent = appStats.unmanaged ?? 0;
  const countHidden = document.getElementById("count-hidden");
  if (countHidden) countHidden.textContent = appStats.hidden ?? 0;
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
    } else if (currentSourceFilter === "repo_system" || currentSourceFilter === "repo_rpm") {
      if (!app.source_type.startsWith("repo_")) {
        return false;
      }
      if (!showHidden && !app.in_menu) {
        return false;
      }
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

    // Build unified, consistent subtitle across all cards
    const subtitle = app.generic_name
      ? (app.comment && app.comment !== app.generic_name ? `${app.generic_name} — ${app.comment}` : app.generic_name)
      : (app.comment || "");

    card.innerHTML = `
      <div>
        <div class="card-top">
          <img class="app-icon" src="${iconUrl}" alt="" loading="lazy" onerror="this.src='/api/icon'">
          <div class="card-header-info">
            <div class="card-title-row">
              <h3 class="app-name" title="${escapeHtml(app.name)}">${escapeHtml(app.name)}</h3>
            </div>
            <div class="app-subtitle" title="${escapeHtml(subtitle || app.name)}">${escapeHtml(subtitle || "")}</div>
            <div class="card-badges">
              <span class="badge ${badgeClass}">${escapeHtml(app.source_label)}</span>
              <span class="badge ${menuBadgeClass}">${menuBadgeText}</span>
              ${app.primary_category ? `<span class="badge badge-unmanaged">${escapeHtml(app.primary_category)}</span>` : ""}
            </div>
          </div>
        </div>
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
          <div class="card-uninstall-block" style="margin-top: 6px;">
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
      <td class="col-install-type">
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
      <td class="col-menu-status">
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

// ==========================================
// Settings, Theme & Customization Engine
// ==========================================

function loadSavedSettings() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY) || localStorage.getItem(LEGACY_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      userSettings = {
        themeId: parsed.themeId || "catppuccin",
        density: parsed.density || "standard",
        fontScale: typeof parsed.fontScale === "number" ? parsed.fontScale : 100,
        customColors: parsed.customColors || null,
        savedCustomThemes: parsed.savedCustomThemes || {},
        showServicesLink: Boolean(parsed.showServicesLink),
        openServicesInSameTab: Boolean(parsed.openServicesInSameTab),
        servicesDashboardUrl: parsed.servicesDashboardUrl || "http://localhost:5100",
        showGitHubBtn: parsed.showGitHubBtn !== undefined ? Boolean(parsed.showGitHubBtn) : true,
        checkForUpdates: parsed.checkForUpdates !== undefined ? Boolean(parsed.checkForUpdates) : true
      };
    }
  } catch (err) {
    console.warn("Could not load saved settings:", err);
  }
}

function saveSettingsToStorage() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(userSettings));
  } catch (err) {
    console.warn("Could not save settings:", err);
  }
}

function applyAllActiveSettings() {
  // Apply Theme
  if (userSettings.themeId === "custom" && userSettings.customColors) {
    applyThemeColors(userSettings.customColors);
  } else if (PRESET_THEMES[userSettings.themeId]) {
    applyThemeColors(PRESET_THEMES[userSettings.themeId].colors);
  } else if (userSettings.savedCustomThemes && userSettings.savedCustomThemes[userSettings.themeId]) {
    applyThemeColors(userSettings.savedCustomThemes[userSettings.themeId]);
  } else {
    applyThemeColors(PRESET_THEMES["catppuccin"].colors);
  }

  // Apply Density
  applyDensity(userSettings.density);

  // Apply Font Scale
  applyFontScale(userSettings.fontScale);

  // Apply Services Nav Link
  applyServicesNav();

  // Apply GitHub Nav Button
  applyGitHubNav();
}

function applyGitHubNav() {
  const link = document.getElementById("nav-github-link");
  if (link) {
    link.style.display = userSettings.showGitHubBtn ? "inline-flex" : "none";
  }
}

function checkAppUpdatesAsync(force = false) {
  if (!userSettings.checkForUpdates) {
    clearUpdateIndicator();
    return;
  }

  const url = force ? "/api/check-update?force=1" : "/api/check-update";
  fetch(url)
    .then((r) => r.json())
    .then((data) => {
      appUpdateData = data;
      renderUpdateUI(data);
    })
    .catch((err) => {
      console.warn("Could not check for updates:", err);
    });
}

function renderUpdateUI(data) {
  if (data) {
    appUpdateData = data;
  }
  const ghLink = document.getElementById("nav-github-link");
  const navBadge = document.getElementById("nav-update-badge");
  const statusBadge = document.getElementById("update-status-badge");
  const banner = document.getElementById("settings-update-banner");
  const bannerVer = document.getElementById("update-banner-version");
  const bannerLink = document.getElementById("update-banner-link");

  if (!userSettings.checkForUpdates) {
    clearUpdateIndicator();
    return;
  }

  if (data && data.has_update) {
    const cleanVer = data.latest_version.startsWith("v") ? data.latest_version : `v${data.latest_version}`;
    const dismissedVer = localStorage.getItem("appindex_dismissed_update_version");
    const isDismissed = Boolean(
      dismissedVer && (dismissedVer === data.latest_version || dismissedVer === cleanVer || `v${dismissedVer}` === cleanVer)
    );

    if (ghLink) {
      if (!isDismissed) {
        ghLink.classList.add("has-update");
        if (data.release_url) ghLink.href = data.release_url;
        ghLink.title = `Update available (${data.latest_version}) - Click to view release`;
      } else {
        ghLink.classList.remove("has-update");
        ghLink.href = "https://github.com/PlasmaDrifter/AppIndex";
        ghLink.title = "GitHub Repository";
      }
    }
    if (navBadge) {
      navBadge.style.display = isDismissed ? "none" : "flex";
      navBadge.title = `Update available: ${data.latest_version}`;
    }
    if (statusBadge) {
      statusBadge.style.display = "inline-block";
      statusBadge.textContent = `${cleanVer} available`;
    }
    if (banner) {
      if (!isDismissed) {
        banner.style.display = "flex";
        if (bannerVer) bannerVer.textContent = cleanVer;
        if (bannerLink && data.release_url) bannerLink.href = data.release_url;
      } else {
        banner.style.display = "none";
      }
    }
  } else {
    clearUpdateIndicator();
  }
}

function clearUpdateIndicator() {
  const ghLink = document.getElementById("nav-github-link");
  const navBadge = document.getElementById("nav-update-badge");
  const statusBadge = document.getElementById("update-status-badge");
  const banner = document.getElementById("settings-update-banner");

  if (ghLink) {
    ghLink.classList.remove("has-update");
    ghLink.href = "https://github.com/PlasmaDrifter/AppIndex";
    ghLink.title = "GitHub Repository";
  }
  if (navBadge) {
    navBadge.style.display = "none";
  }
  if (statusBadge) {
    statusBadge.style.display = "none";
  }
  if (banner) {
    banner.style.display = "none";
  }
}

function applyServicesNav() {
  const link = document.getElementById("nav-services-link");
  if (!link) return;
  const show = Boolean(userSettings.showServicesLink);
  link.style.display = show ? "inline-flex" : "none";
  link.href = userSettings.servicesDashboardUrl || "http://localhost:5100";

  const arrow = link.querySelector(".nav-external-arrow");
  if (userSettings.openServicesInSameTab) {
    link.target = "_self";
    link.removeAttribute("rel");
    if (arrow) arrow.style.display = "none";
  } else {
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    if (arrow) arrow.style.display = "";
  }
}

function applyThemeColors(colorsObj) {
  if (!colorsObj) return;
  const root = document.documentElement;
  const savedFontScale = root.style.getPropertyValue("--font-scale");
  root.removeAttribute("style");
  if (savedFontScale) {
    root.style.setProperty("--font-scale", savedFontScale);
  }
  for (const [key, val] of Object.entries(colorsObj)) {
    root.style.setProperty(key, val);
  }
}

function applyDensity(density) {
  if (!document.body) return;
  document.body.classList.remove("density-compact", "density-standard", "density-spacious");
  document.body.classList.add("density-" + (density || "standard"));
}

function applyFontScale(scaleVal) {
  const scale = (scaleVal || 100) / 100;
  document.documentElement.style.setProperty("--font-scale", scale.toString());
}

function initSettingsUI() {
  renderPresetThemeGrid();
  renderSavedCustomThemes();
  syncSettingsUI();
  bindSettingsInteractiveEvents();
}

function syncSettingsUI() {
  // Sync Density Buttons
  const selector = document.getElementById("density-selector");
  if (selector) {
    selector.querySelectorAll(".density-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.density === userSettings.density);
    });
  }

  // Sync Font Scale
  const slider = document.getElementById("font-scale-slider");
  const valBadge = document.getElementById("font-scale-value");
  if (slider) slider.value = userSettings.fontScale;
  if (valBadge) valBadge.textContent = userSettings.fontScale + "%";

  // Sync Color Pickers
  let currentColors = {};
  if (userSettings.themeId === "custom" && userSettings.customColors) {
    currentColors = userSettings.customColors;
  } else if (PRESET_THEMES[userSettings.themeId]) {
    currentColors = PRESET_THEMES[userSettings.themeId].colors;
  } else if (userSettings.savedCustomThemes && userSettings.savedCustomThemes[userSettings.themeId]) {
    currentColors = userSettings.savedCustomThemes[userSettings.themeId];
  } else {
    currentColors = PRESET_THEMES["catppuccin"].colors;
  }
  updateColorPickersUI(currentColors);

  // Highlight Active Theme Card
  const presetsGrid = document.getElementById("theme-presets-grid");
  if (presetsGrid) {
    presetsGrid.querySelectorAll(".theme-preset-card").forEach((card) => {
      card.classList.toggle("active", card.dataset.themeId === userSettings.themeId);
    });
  }

  // Sync Companion Tools (Services Dashboard)
  const toggleServices = document.getElementById("toggle-services-link");
  const toggleSameTab = document.getElementById("toggle-services-same-tab");
  const sameTabWrapper = document.getElementById("companion-same-tab-wrapper");
  const servicesUrlConfig = document.getElementById("services-url-config");
  const servicesUrlInput = document.getElementById("services-url-input");
  const toggleGitHub = document.getElementById("toggle-github-btn");
  const toggleUpdates = document.getElementById("toggle-check-updates");

  if (toggleGitHub) {
    toggleGitHub.checked = Boolean(userSettings.showGitHubBtn);
  }
  if (toggleUpdates) {
    toggleUpdates.checked = Boolean(userSettings.checkForUpdates);
  }
  const statusBadge = document.getElementById("update-status-badge");
  if (statusBadge) {
    if (userSettings.checkForUpdates && appUpdateData && appUpdateData.has_update) {
      statusBadge.style.display = "inline-block";
      const cleanVer = appUpdateData.latest_version.startsWith("v") ? appUpdateData.latest_version : `v${appUpdateData.latest_version}`;
      statusBadge.textContent = `${cleanVer} available`;
    } else {
      statusBadge.style.display = "none";
    }
  }

  const banner = document.getElementById("settings-update-banner");
  const bannerVer = document.getElementById("update-banner-version");
  const bannerLink = document.getElementById("update-banner-link");
  if (banner) {
    if (userSettings.checkForUpdates && appUpdateData && appUpdateData.has_update) {
      const cleanVer = appUpdateData.latest_version.startsWith("v") ? appUpdateData.latest_version : `v${appUpdateData.latest_version}`;
      const dismissedVer = localStorage.getItem("appindex_dismissed_update_version");
      const isDismissed = Boolean(
        dismissedVer && (dismissedVer === appUpdateData.latest_version || dismissedVer === cleanVer || `v${dismissedVer}` === cleanVer)
      );
      if (!isDismissed) {
        banner.style.display = "flex";
        if (bannerVer) bannerVer.textContent = cleanVer;
        if (bannerLink && appUpdateData.release_url) bannerLink.href = appUpdateData.release_url;
      } else {
        banner.style.display = "none";
      }
    } else {
      banner.style.display = "none";
    }
  }
  if (toggleServices) {
    toggleServices.checked = Boolean(userSettings.showServicesLink);
  }
  if (toggleSameTab) {
    toggleSameTab.checked = Boolean(userSettings.openServicesInSameTab);
  }
  if (sameTabWrapper) {
    if (userSettings.showServicesLink) {
      sameTabWrapper.classList.remove("toggle-disabled");
      if (toggleSameTab) toggleSameTab.disabled = false;
    } else {
      sameTabWrapper.classList.add("toggle-disabled");
      if (toggleSameTab) toggleSameTab.disabled = true;
    }
  }
  if (servicesUrlConfig) {
    servicesUrlConfig.style.display = userSettings.showServicesLink ? "flex" : "none";
  }
  if (servicesUrlInput) {
    servicesUrlInput.value = userSettings.servicesDashboardUrl || "http://localhost:5100";
  }
}

function renderPresetThemeGrid() {
  const presetsGrid = document.getElementById("theme-presets-grid");
  if (!presetsGrid) return;
  presetsGrid.innerHTML = "";

  Object.entries(PRESET_THEMES).forEach(([themeId, theme]) => {
    const card = document.createElement("div");
    card.className = "theme-preset-card" + (userSettings.themeId === themeId ? " active" : "");
    card.dataset.themeId = themeId;

    const swatchesHtml = theme.swatches
      .map((hex) => `<span class="theme-swatch-circle" style="background-color: ${hex};"></span>`)
      .join("");

    card.innerHTML = `
      <div class="theme-swatches">${swatchesHtml}</div>
      <span class="theme-name" title="${theme.name}">${theme.name}</span>
    `;

    card.addEventListener("click", () => {
      userSettings.themeId = themeId;
      userSettings.customColors = null;
      applyThemeColors(theme.colors);
      updateColorPickersUI(theme.colors);
      saveSettingsToStorage();
      syncSettingsUI();
      showToast(`Applied ${theme.name}`);
    });

    presetsGrid.appendChild(card);
  });
}

function updateColorPickersUI(colorsObj) {
  COLOR_PICKER_MAP.forEach(({ inputId, hexId, varName }) => {
    const input = document.getElementById(inputId);
    const hexSpan = document.getElementById(hexId);
    if (!input) return;

    let hexVal = colorsObj[varName];
    if (!hexVal) {
      hexVal = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
    }
    hexVal = normalizeHex(hexVal);

    input.value = hexVal;
    if (hexSpan) hexSpan.textContent = hexVal.toUpperCase();
  });
}

function normalizeHex(colorStr) {
  if (!colorStr) return "#000000";
  colorStr = colorStr.trim();
  if (colorStr.startsWith("#") && (colorStr.length === 7 || colorStr.length === 4)) {
    if (colorStr.length === 4) {
      return "#" + colorStr[1] + colorStr[1] + colorStr[2] + colorStr[2] + colorStr[3] + colorStr[3];
    }
    return colorStr;
  }
  const match = colorStr.match(/\d+/g);
  if (match && match.length >= 3) {
    const r = parseInt(match[0], 10).toString(16).padStart(2, "0");
    const g = parseInt(match[1], 10).toString(16).padStart(2, "0");
    const b = parseInt(match[2], 10).toString(16).padStart(2, "0");
    return `#${r}${g}${b}`;
  }
  return "#000000";
}

function renderSavedCustomThemes() {
  const customSection = document.getElementById("saved-custom-themes-section");
  const list = document.getElementById("saved-themes-list");
  if (!list || !customSection) return;
  const names = Object.keys(userSettings.savedCustomThemes || {});

  if (names.length === 0) {
    customSection.style.display = "none";
    list.innerHTML = "";
    return;
  }

  customSection.style.display = "block";
  list.innerHTML = "";

  names.forEach((name) => {
    const chip = document.createElement("div");
    chip.className = "saved-theme-chip" + (userSettings.themeId === name ? " active" : "");

    chip.innerHTML = `
      <button class="saved-theme-apply-btn">${escapeHtml(name)}</button>
      <button class="saved-theme-del-btn" title="Delete theme">&times;</button>
    `;

    chip.querySelector(".saved-theme-apply-btn").addEventListener("click", () => {
      userSettings.themeId = name;
      userSettings.customColors = null;
      const themeColors = userSettings.savedCustomThemes[name];
      applyThemeColors(themeColors);
      updateColorPickersUI(themeColors);
      saveSettingsToStorage();
      syncSettingsUI();
      renderSavedCustomThemes();
      showToast(`Applied custom theme: ${name}`);
    });

    chip.querySelector(".saved-theme-del-btn").addEventListener("click", (e) => {
      e.stopPropagation();
      delete userSettings.savedCustomThemes[name];
      if (userSettings.themeId === name) {
        userSettings.themeId = "catppuccin";
        applyThemeColors(PRESET_THEMES["catppuccin"].colors);
      }
      saveSettingsToStorage();
      syncSettingsUI();
      renderSavedCustomThemes();
      showToast(`Deleted theme: ${name}`);
    });

    list.appendChild(chip);
  });
}

function bindSettingsInteractiveEvents() {
  // Density selector buttons
  const selector = document.getElementById("density-selector");
  if (selector) {
    selector.querySelectorAll(".density-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const density = btn.dataset.density;
        userSettings.density = density;
        applyDensity(density);
        saveSettingsToStorage();
        syncSettingsUI();
      });
    });
  }

  // Font scale slider
  const slider = document.getElementById("font-scale-slider");
  const valBadge = document.getElementById("font-scale-value");
  if (slider) {
    slider.addEventListener("input", (e) => {
      const val = parseInt(e.target.value, 10);
      userSettings.fontScale = val;
      applyFontScale(val);
      if (valBadge) valBadge.textContent = val + "%";
      saveSettingsToStorage();
    });
  }

  // Quick tick buttons
  document.querySelectorAll(".tick-btn").forEach((tick) => {
    tick.addEventListener("click", () => {
      const val = parseInt(tick.dataset.val, 10);
      userSettings.fontScale = val;
      applyFontScale(val);
      if (slider) slider.value = val;
      if (valBadge) valBadge.textContent = val + "%";
      saveSettingsToStorage();
    });
  });

  // Color pickers input
  COLOR_PICKER_MAP.forEach(({ inputId, hexId, varName }) => {
    const input = document.getElementById(inputId);
    const hexSpan = document.getElementById(hexId);
    if (!input) return;

    input.addEventListener("input", (e) => {
      const val = e.target.value;
      if (hexSpan) hexSpan.textContent = val.toUpperCase();
      document.documentElement.style.setProperty(varName, val);

      if (!userSettings.customColors) {
        userSettings.customColors = {};
      }
      userSettings.customColors[varName] = val;
      userSettings.themeId = "custom";

      // If card color changed, adjust card hover
      if (varName === "--bg-card") {
        document.documentElement.style.setProperty("--bg-card-hover", val);
      }

      saveSettingsToStorage();
      const presetsGrid = document.getElementById("theme-presets-grid");
      if (presetsGrid) {
        presetsGrid.querySelectorAll(".theme-preset-card").forEach((c) => c.classList.remove("active"));
      }
    });
  });

  // Save custom theme button
  const btnSaveTheme = document.getElementById("btn-save-custom-theme");
  const nameInput = document.getElementById("custom-theme-name");
  if (btnSaveTheme && nameInput) {
    btnSaveTheme.addEventListener("click", () => {
      const name = nameInput.value.trim();
      if (!name) {
        showToast("Please enter a name for the custom theme");
        nameInput.focus();
        return;
      }

      const colors = {};
      COLOR_PICKER_MAP.forEach(({ inputId, varName }) => {
        const input = document.getElementById(inputId);
        if (input) colors[varName] = input.value;
      });

      // Include hover
      colors["--bg-card-hover"] = colors["--bg-card"];

      userSettings.savedCustomThemes[name] = colors;
      userSettings.themeId = name;
      userSettings.customColors = null;
      nameInput.value = "";

      saveSettingsToStorage();
      syncSettingsUI();
      renderSavedCustomThemes();
      showToast(`Custom theme "${name}" saved!`);
    });
  }

  // Reset to default button
  const btnReset = document.getElementById("btn-reset-theme");
  if (btnReset) {
    btnReset.addEventListener("click", () => {
      userSettings.themeId = "catppuccin";
      userSettings.density = "standard";
      userSettings.fontScale = 100;
      userSettings.customColors = null;
      userSettings.showGitHubBtn = true;
      userSettings.checkForUpdates = true;
      userSettings.showServicesLink = false;
      userSettings.openServicesInSameTab = false;
      userSettings.servicesDashboardUrl = "http://localhost:5100";

      localStorage.removeItem("appindex_dismissed_update_version");
      applyAllActiveSettings();
      syncSettingsUI();
      saveSettingsToStorage();
      if (userSettings.checkForUpdates) {
        checkAppUpdatesAsync();
      }
      showToast("Reset all settings to default");
    });
  }

  // Settings Update Banner Clear
  const btnDismissBanner = document.getElementById("btn-dismiss-update-banner");
  if (btnDismissBanner) {
    btnDismissBanner.addEventListener("click", () => {
      const banner = document.getElementById("settings-update-banner");
      if (banner) banner.style.display = "none";

      const navBadge = document.getElementById("nav-update-badge");
      if (navBadge) navBadge.style.display = "none";

      const ghLink = document.getElementById("nav-github-link");
      if (ghLink) {
        ghLink.classList.remove("has-update");
        ghLink.href = "https://github.com/PlasmaDrifter/AppIndex";
        ghLink.title = "GitHub Repository";
      }

      if (appUpdateData && appUpdateData.latest_version) {
        localStorage.setItem("appindex_dismissed_update_version", appUpdateData.latest_version);
      }
      showToast("Update notification cleared");
    });
  }

  // Navigation Links & Updates
  const toggleGitHub = document.getElementById("toggle-github-btn");
  const toggleUpdates = document.getElementById("toggle-check-updates");

  if (toggleGitHub) {
    toggleGitHub.addEventListener("change", (e) => {
      userSettings.showGitHubBtn = e.target.checked;
      applyGitHubNav();
      saveSettingsToStorage();
    });
  }

  if (toggleUpdates) {
    toggleUpdates.addEventListener("change", (e) => {
      userSettings.checkForUpdates = e.target.checked;
      saveSettingsToStorage();
      if (e.target.checked) {
        checkAppUpdatesAsync();
      } else {
        clearUpdateIndicator();
      }
    });
  }

  // Companion Tools (Services Dashboard)
  const toggleServices = document.getElementById("toggle-services-link");
  const toggleSameTab = document.getElementById("toggle-services-same-tab");
  const sameTabWrapper = document.getElementById("companion-same-tab-wrapper");
  const servicesUrlConfig = document.getElementById("services-url-config");
  const servicesUrlInput = document.getElementById("services-url-input");
  const btnResetUrl = document.getElementById("btn-reset-services-url");
  const helpBtn = document.getElementById("services-help-btn");
  const helpPopover = document.getElementById("services-help-popover");

  if (toggleServices) {
    toggleServices.addEventListener("change", (e) => {
      userSettings.showServicesLink = e.target.checked;
      if (servicesUrlConfig) {
        servicesUrlConfig.style.display = e.target.checked ? "flex" : "none";
      }
      if (sameTabWrapper) {
        if (e.target.checked) {
          sameTabWrapper.classList.remove("toggle-disabled");
          if (toggleSameTab) toggleSameTab.disabled = false;
        } else {
          sameTabWrapper.classList.add("toggle-disabled");
          if (toggleSameTab) toggleSameTab.disabled = true;
        }
      }
      applyServicesNav();
      saveSettingsToStorage();
    });
  }

  if (toggleSameTab) {
    toggleSameTab.addEventListener("change", (e) => {
      userSettings.openServicesInSameTab = e.target.checked;
      applyServicesNav();
      saveSettingsToStorage();
    });
  }

  if (servicesUrlInput) {
    const handleUrlChange = () => {
      let val = servicesUrlInput.value.trim();
      if (!val) {
        val = "http://localhost:5100";
        servicesUrlInput.value = val;
      }
      userSettings.servicesDashboardUrl = val;
      applyServicesNav();
      saveSettingsToStorage();
    };
    servicesUrlInput.addEventListener("change", handleUrlChange);
    servicesUrlInput.addEventListener("blur", handleUrlChange);
  }

  if (btnResetUrl && servicesUrlInput) {
    btnResetUrl.addEventListener("click", () => {
      servicesUrlInput.value = "http://localhost:5100";
      userSettings.servicesDashboardUrl = "http://localhost:5100";
      applyServicesNav();
      saveSettingsToStorage();
      showToast("Reset Services URL to default");
    });
  }

  const setupPopover = (btnId, popoverId) => {
    const btn = document.getElementById(btnId);
    const popover = document.getElementById(popoverId);
    if (!btn || !popover) return;

    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const isVisible = popover.style.display === "block";

      document.querySelectorAll(".help-popover").forEach((p) => (p.style.display = "none"));
      document.querySelectorAll(".help-circle-btn").forEach((b) => b.classList.remove("active"));

      popover.style.display = isVisible ? "none" : "block";
      btn.classList.toggle("active", !isVisible);
    });
  };

  setupPopover("services-help-btn", "services-help-popover");
  setupPopover("github-help-btn", "github-help-popover");

  document.addEventListener("click", (e) => {
    document.querySelectorAll(".help-popover").forEach((popover) => {
      if (!popover.contains(e.target)) {
        popover.style.display = "none";
      }
    });
    document.querySelectorAll(".help-circle-btn").forEach((btn) => {
      if (!btn.contains(e.target)) {
        btn.classList.remove("active");
      }
    });
  });
}

function openSettingsModal() {
  const modal = document.getElementById("settings-modal");
  if (modal) {
    document.querySelectorAll(".help-popover").forEach((p) => (p.style.display = "none"));
    document.querySelectorAll(".help-circle-btn").forEach((b) => b.classList.remove("active"));

    syncSettingsUI();
    modal.style.display = "flex";
  }
}

function closeSettingsModal() {
  const modal = document.getElementById("settings-modal");
  if (modal) {
    document.querySelectorAll(".help-popover").forEach((p) => (p.style.display = "none"));
    document.querySelectorAll(".help-circle-btn").forEach((b) => b.classList.remove("active"));

    modal.style.display = "none";
  }
}

// ==========================================
// Custom Export Options Logic
// ==========================================

function openExportModal() {
  const modal = document.getElementById("export-modal");
  if (modal) {
    modal.style.display = "flex";
    try {
      updateExportSummary();
    } catch (err) {
      console.error("Error updating export summary:", err);
    }
  }
}

function closeExportModal() {
  const modal = document.getElementById("export-modal");
  if (modal) {
    modal.style.display = "none";
  }
}

function getExportFilterState() {
  const format = document.querySelector('input[name="export-format"]:checked')?.value || "csv";

  const sourceCheckboxes = document.querySelectorAll('input[name="export-source"]:checked');
  const selectedSources = [];
  sourceCheckboxes.forEach((cb) => {
    cb.value.split(",").forEach((s) => {
      const trimmed = s.trim().toLowerCase();
      if (trimmed && !selectedSources.includes(trimmed)) selectedSources.push(trimmed);
    });
  });

  const visibility = document.querySelector('input[name="export-visibility"]:checked')?.value || "all";

  const fieldCheckboxes = document.querySelectorAll('input[name="export-field"]:checked');
  const selectedFields = Array.from(fieldCheckboxes).map((cb) => cb.value);

  return { format, selectedSources, visibility, selectedFields };
}

function updateExportSummary() {
  const { format, selectedSources, visibility, selectedFields } = getExportFilterState();

  let matchingCount = 0;
  if (Array.isArray(allApps)) {
    allApps.forEach((app) => {
      const src = (app.source_type || "").toLowerCase();
      if (!selectedSources.includes(src)) return;
      if (visibility === "in_menu" && !app.in_menu) return;
      if (visibility === "hidden" && app.in_menu) return;
      matchingCount++;
    });
  }

  const summaryEl = document.getElementById("export-summary-text");
  const btnLabelEl = document.getElementById("export-btn-label");
  const doExportBtn = document.getElementById("btn-do-export");

  if (summaryEl) {
    summaryEl.textContent = `Will export ${matchingCount} application${matchingCount === 1 ? "" : "s"} with ${selectedFields.length} field${selectedFields.length === 1 ? "" : "s"} as ${format.toUpperCase()}`;
  }
  if (btnLabelEl) {
    btnLabelEl.textContent = `Download ${format.toUpperCase()}`;
  }
  if (doExportBtn) {
    doExportBtn.disabled = matchingCount === 0 || selectedFields.length === 0;
  }
}

function executeCustomExport() {
  const { format, selectedSources, visibility, selectedFields } = getExportFilterState();
  if (selectedFields.length === 0) {
    showToast("Please select at least one field to export");
    return;
  }
  if (selectedSources.length === 0) {
    showToast("Please select at least one package source to export");
    return;
  }

  const params = new URLSearchParams();
  params.set("format", format);
  params.set("fields", selectedFields.join(","));
  params.set("sources", selectedSources.join(","));
  params.set("visibility", visibility);

  const exportUrl = `/api/export?${params.toString()}`;
  const filename = `installed_apps.${format}`;

  const link = document.createElement("a");
  link.href = exportUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);

  closeExportModal();
  showToast(`Downloading custom ${format.toUpperCase()} export...`);
}

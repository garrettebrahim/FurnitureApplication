// Furniture Planner frontend
(function () {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  // -------------------- Tabs --------------------
  $$(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      const tab = btn.dataset.tab;
      $$(".tab").forEach((b) => b.classList.toggle("active", b === btn));
      $$(".tab-panel").forEach((p) =>
        p.classList.toggle("active", p.dataset.panel === tab)
      );
    });
  });

  // -------------------- Category filter --------------------
  function applyCategoryFilter(cat) {
    $$(".cat-link").forEach((l) => l.classList.toggle("active", l.dataset.cat === cat));
    $$(".cat-chip").forEach((c) => c.classList.toggle("active", c.dataset.cat === cat));
    $$(".card[data-cat]").forEach((card) => {
      card.style.display = cat === "__all__" || card.dataset.cat === cat ? "" : "none";
    });
  }
  $$(".cat-link").forEach((link) => {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      applyCategoryFilter(link.dataset.cat);
    });
  });
  // Clicking a chip in the totals strip filters too; clicking the active
  // chip again clears the filter back to "All".
  $$(".cat-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const cat = chip.classList.contains("active") ? "__all__" : chip.dataset.cat;
      applyCategoryFilter(cat);
    });
  });

  // -------------------- Add-item modal --------------------
  const modal = $("#add-modal");
  const preview = $("#scrape-preview");
  const statusEl = $("#scrape-status");
  const warningsEl = $("#scrape-warnings");

  function openModal() {
    modal.classList.remove("hidden");
    $("#add-url").value = "";
    preview.classList.add("hidden");
    statusEl.textContent = "";
    warningsEl.innerHTML = "";
    $("#add-needs-assembly").checked = false;
    $("#add-assembly-days").value = 1;
    $("#add-assembly-days-wrap").classList.add("hidden");
  }
  function closeModal() {
    modal.classList.add("hidden");
  }
  $("#add-item-btn").addEventListener("click", openModal);
  $("#cancel-btn").addEventListener("click", closeModal);
  // Only close on true backdrop clicks — never when the click landed on or
  // bubbled up from anything inside the modal card.
  modal.addEventListener("click", (e) => {
    if (!e.target.closest(".modal-card")) closeModal();
  });

  // -------------------- Scrape --------------------
  $("#scrape-btn").addEventListener("click", async () => {
    const url = $("#add-url").value.trim();
    if (!url) {
      statusEl.textContent = "Please paste a URL first.";
      return;
    }
    statusEl.textContent = "Fetching product details…";
    warningsEl.innerHTML = "";
    try {
      const res = await fetch("/api/scrape", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url,
          needs_assembly: $("#add-needs-assembly").checked,
          assembly_days: parseInt($("#add-assembly-days").value, 10) || 1,
        }),
      });
      const data = await res.json();
      populatePreview(data);
      statusEl.textContent = data.ok
        ? "Loaded. Review the details below."
        : "Couldn't fully load the page — please review and fill in any missing fields.";
    } catch (err) {
      statusEl.textContent = "Network error: " + err.message;
    }
  });

  function populatePreview(data) {
    preview.classList.remove("hidden");
    $("#add-name").value = data.name || "";
    $("#add-price").value = data.price != null ? data.price : "";
    $("#add-store").value = data.store || "generic";
    $("#add-ship-min").value = data.ship_days_min != null ? data.ship_days_min : "";
    $("#add-ship-max").value = data.ship_days_max != null ? data.ship_days_max : "";
    const srcLabel =
      data.ship_source === "page"
        ? "✓ Detected from product page"
        : data.ship_source === "default"
        ? "⚠ Using store default"
        : "Manual entry";
    $("#ship-source").textContent = data.ship_source_detail
      ? `${srcLabel} — ${data.ship_source_detail}`
      : srcLabel;
    const img = $("#preview-img");
    if (data.image_url) {
      img.src = data.image_url;
      img.style.display = "";
      // Track the canonical URL so the save handler doesn't fall back to
      // the browser's resolved .src (which can be the page URL).
      preview.dataset.imageUrl = data.image_url;
    } else {
      img.removeAttribute("src");
      img.style.display = "none";
      delete preview.dataset.imageUrl;
    }
    updateWindowPreview();

    warningsEl.innerHTML = "";
    (data.warnings || []).forEach((w) => {
      const div = document.createElement("div");
      div.className = "warn";
      div.textContent = w;
      warningsEl.appendChild(div);
    });
    preview.dataset.sourceUrl = data.url || "";
  }

  // -------------------- Live arrival + order window preview --------------------
  let previewTimer = null;
  async function updateWindowPreview() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(async () => {
      const smin = parseInt($("#add-ship-min").value, 10);
      const smax = parseInt($("#add-ship-max").value, 10);
      const needsA = $("#add-needs-assembly").checked;
      const aDays = parseInt($("#add-assembly-days").value, 10) || 1;
      try {
        const res = await fetch("/api/preview-window", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            ship_days_min: Number.isNaN(smin) ? null : smin,
            ship_days_max: Number.isNaN(smax) ? null : smax,
            needs_assembly: needsA,
            assembly_days: aDays,
          }),
        });
        const d = await res.json();
        $("#arrival-window-preview").textContent = d.arrival_label || "";
        $("#order-window-preview").textContent = d.order_window_label || "";
      } catch (_) {
        $("#order-window-preview").textContent = "";
      }
    }, 120);
  }
  ["#add-ship-min", "#add-ship-max", "#add-assembly-days"].forEach((sel) =>
    $(sel).addEventListener("input", updateWindowPreview)
  );
  $("#add-needs-assembly").addEventListener("change", (e) => {
    $("#add-assembly-days-wrap").classList.toggle("hidden", !e.target.checked);
    updateWindowPreview();
  });

  // -------------------- Save new item --------------------
  $("#save-btn").addEventListener("click", async () => {
    const payload = {
      source_url: $("#add-url").value.trim(),
      store: $("#add-store").value,
      name: $("#add-name").value.trim(),
      price: parseFloat($("#add-price").value) || null,
      image_url: preview.dataset.imageUrl || null,
      list_type: $("#add-list").value,
      category: $("#add-category").value,
      ship_days_min: parseInt($("#add-ship-min").value, 10),
      ship_days_max: parseInt($("#add-ship-max").value, 10),
      ship_source: $("#ship-source").textContent.includes("page") ? "page" : "default",
      needs_assembly: $("#add-needs-assembly").checked,
      assembly_days: parseInt($("#add-assembly-days").value, 10) || 1,
      notes: $("#add-notes").value.trim(),
    };
    if (!payload.name) {
      alert("Name is required.");
      return;
    }
    const res = await fetch("/api/items", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      alert("Save failed: HTTP " + res.status);
      return;
    }
    closeModal();
    location.reload();
  });

  // -------------------- Ship-days mini-modal --------------------
  const shipModal = $("#ship-modal");
  if (shipModal) {
    $("#ship-modal-cancel").addEventListener("click", () => shipModal.classList.add("hidden"));
    shipModal.addEventListener("click", (e) => {
      if (!e.target.closest(".modal-card")) shipModal.classList.add("hidden");
    });
    $("#ship-modal-save").addEventListener("click", async () => {
      const id = $("#ship-modal-id").value;
      const smin = parseInt($("#ship-modal-min").value, 10);
      const smax = parseInt($("#ship-modal-max").value, 10);
      if (Number.isNaN(smin) || Number.isNaN(smax) || smax < smin) {
        alert("Min and max must be numbers and min ≤ max.");
        return;
      }
      const res = await fetch(`/api/items/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ship_days_min: smin,
          ship_days_max: smax,
          ship_source: $("#ship-modal-verified").checked ? "manual" : "default",
        }),
      });
      if (res.ok) location.reload();
    });
  }

  // -------------------- Card actions --------------------
  document.addEventListener("change", async (e) => {
    if (e.target.matches(".move-list")) {
      const id = e.target.dataset.id;
      const lt = e.target.value;
      const res = await fetch(`/api/items/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ list_type: lt }),
      });
      if (res.ok) location.reload();
    }
  });

  document.addEventListener("click", async (e) => {
    if (e.target.matches(".delete-item")) {
      if (!confirm("Delete this item?")) return;
      const id = e.target.dataset.id;
      const res = await fetch(`/api/items/${id}`, { method: "DELETE" });
      if (res.ok) location.reload();
    }
    if (e.target.matches(".edit-ship")) {
      const id = e.target.dataset.id;
      $("#ship-modal-id").value = id;
      $("#ship-modal-min").value = e.target.dataset.min || "";
      $("#ship-modal-max").value = e.target.dataset.max || "";
      $("#ship-modal-verified").checked = true;
      $("#ship-modal").classList.remove("hidden");
    }
    if (e.target.matches(".edit-assembly")) {
      const id = e.target.dataset.id;
      const currentlyNeeds = e.target.dataset.needs === "1";
      const ans = confirm(
        currentlyNeeds
          ? "This item is marked as needing assembly. OK to keep, Cancel to remove the assembly flag."
          : "Mark this item as needing assembly? OK to add, Cancel to leave as-is."
      );
      let needs_assembly = ans ? true : !currentlyNeeds ? false : false;
      // If toggling off when already on, ans=false above means user wants to remove.
      if (currentlyNeeds && !ans) needs_assembly = false;
      if (!currentlyNeeds && !ans) return; // no-op

      let assembly_days = parseInt(e.target.dataset.days, 10) || 1;
      if (needs_assembly) {
        const d = prompt("How many days do you need to build it?", String(assembly_days));
        if (d === null) return;
        assembly_days = Math.max(1, parseInt(d, 10) || 1);
      } else {
        assembly_days = 0;
      }
      const res = await fetch(`/api/items/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ needs_assembly, assembly_days }),
      });
      if (res.ok) location.reload();
    }
  });
})();

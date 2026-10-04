document.addEventListener("DOMContentLoaded", () => {
  const revealTargets = document.querySelectorAll(
    ".section-heading, .portfolio-card, .vault-copy, .vault-card, .service-card, .contact-inner"
  );
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  if (reducedMotion || !("IntersectionObserver" in window)) {
    revealTargets.forEach((element) => element.classList.add("is-visible"));
  } else {
    const revealObserver = new IntersectionObserver(
      (entries, observer) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.12, rootMargin: "0px 0px -36px 0px" }
    );

    revealTargets.forEach((element) => {
      element.classList.add("scroll-reveal");
      revealObserver.observe(element);
    });
  }

  const filters = Array.from(document.querySelectorAll("[data-filter]"));
  const cards = Array.from(document.querySelectorAll(".portfolio-card"));
  const emptyMessage = document.querySelector(".filter-empty");

  filters.forEach((button) => {
    button.addEventListener("click", () => {
      const category = button.dataset.filter;
      let visibleCount = 0;
      filters.forEach((filter) => filter.classList.toggle("is-active", filter === button));
      cards.forEach((card) => {
        const visible = category === "all" || card.dataset.category === category;
        card.hidden = !visible;
        if (visible) visibleCount += 1;
      });
      if (emptyMessage) emptyMessage.hidden = visibleCount !== 0;
    });
  });

  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  const portfolioModal = document.getElementById("portfolio-edit-modal");
  const vaultModal = document.getElementById("vault-edit-modal");
  const portfolioForm = document.getElementById("portfolio-edit-form");
  const vaultForm = document.getElementById("vault-edit-form");

  if (portfolioModal && portfolioForm) {
    document.querySelectorAll(".admin-list-item .edit-button").forEach((btn) => {
      if (btn.closest(".admin-section") && btn.closest(".admin-section").querySelector("#portfolio-edit-modal")) {
        btn.addEventListener("click", async () => {
          const itemId = btn.dataset.id;
          const item = btn.closest(".admin-list-item");
          portfolioForm._item_id.value = itemId;
          portfolioForm.title.value = item.querySelector("h3").textContent;
          portfolioForm.category.value = btn.dataset.category;
          portfolioForm.description.value = item.querySelector("p").textContent;
          portfolioForm.tags.value = btn.dataset.tags || "";
          portfolioForm.querySelector("#edit-work-current-file").textContent =
            btn.dataset.downloadFilename
              ? `Current file: ${btn.dataset.downloadFilename}`
              : "No downloadable file attached.";
          portfolioForm.querySelector("#edit-work-file").value = "";
          portfolioForm.querySelector('[name="remove_download"]').checked = false;
          portfolioForm.action = `/admin/portfolio/${itemId}/edit`;
          portfolioModal.hidden = false;
        });
      }
    });

    portfolioModal.addEventListener("click", (e) => {
      if (e.target === portfolioModal || e.target.closest("[data-modal-close]")) {
        portfolioModal.hidden = true;
      }
    });
  }

  if (vaultModal && vaultForm) {
    document.querySelectorAll(".admin-section:has(#vault-edit-modal) .admin-list-item .edit-button").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const assetId = btn.dataset.id;
        const item = btn.closest(".admin-list-item");
        vaultForm._asset_id.value = assetId;
        vaultForm.title.value = item.querySelector("h3").textContent;
        vaultForm.category.value = item.querySelector(".card-meta").textContent.trim().split("/")[0].trim();
        vaultForm.description.value = item.querySelector("p").textContent;
        vaultForm.tags.value = "";
        vaultForm.action = `/admin/vault/${assetId}/edit`;
        vaultModal.hidden = false;
      });
    });

    vaultModal.addEventListener("click", (e) => {
      if (e.target === vaultModal || e.target.closest("[data-modal-close]")) {
        vaultModal.hidden = true;
      }
    });
  }
});

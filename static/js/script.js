document.addEventListener("DOMContentLoaded", () => {
  const dailyForm = document.querySelector("[data-daily-form]");
  if (dailyForm) {
    const ratePaise = Number(dailyForm.dataset.ratePaise);
    const quantityInputs = Array.from(dailyForm.querySelectorAll("[data-litre-entry]"));
    const amountLabels = Array.from(dailyForm.querySelectorAll("[data-session-amount]"));
    const totalQuantity = dailyForm.querySelector("[data-daily-total-quantity]");
    const totalAmount = dailyForm.querySelector("[data-daily-total-amount]");
    const amountFor = (quantityMl) => {
      let remaining = quantityMl;
      let amount = 0;
      [[1000, 5500], [750, 4150], [500, 3300], [250, 1600]].forEach(([packMl, packPrice]) => {
        const packs = Math.floor(remaining / packMl);
        amount += packs * packPrice;
        remaining -= packs * packMl;
      });
      return amount + Math.round((ratePaise * remaining) / 1000);
    };
    const updateDailyTotals = () => {
      const quantities = quantityInputs.map((input) => Math.round(Number(input.value || 0) * 1000));
      const amounts = quantities.map(amountFor);
      quantityInputs.forEach((input, index) => {
        amountLabels[index].textContent = `₹${(amounts[index] / 100).toFixed(2)}`;
      });
      const totalMl = quantities.reduce((total, quantity) => total + quantity, 0);
      const totalPaise = amounts.reduce((total, amount) => total + amount, 0);
      totalQuantity.textContent = `${(totalMl / 1000).toFixed(3).replace(/\.?0+$/, "") || "0"} L`;
      totalAmount.textContent = `₹${(totalPaise / 100).toFixed(2)}`;
    };
    quantityInputs.forEach((input) => input.addEventListener("input", updateDailyTotals));
    updateDailyTotals();

    const providerInput = dailyForm.querySelector("#provider");
    providerInput.addEventListener("change", () => {
      const dateInput = dailyForm.querySelector("#record-date");
      const query = new URLSearchParams({ date: dateInput.value, provider_id: providerInput.value });
      window.location.href = `/daily?${query.toString()}`;
    });
  }

  const providerSearch = document.querySelector("[data-provider-search]");
  if (providerSearch) {
    const items = Array.from(document.querySelectorAll("[data-provider-item]"));
    const noResults = document.querySelector("[data-provider-no-results]");
    providerSearch.addEventListener("input", () => {
      const query = providerSearch.value.trim().toLocaleLowerCase();
      let visibleCount = 0;
      items.forEach((item) => {
        const matches = item.dataset.providerName.includes(query);
        item.hidden = !matches;
        if (matches) visibleCount += 1;
      });
      noResults.hidden = visibleCount > 0;
    });
  }

  document.querySelectorAll("[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  const loadDateButton = document.querySelector("[data-load-date]");
  if (loadDateButton) {
    loadDateButton.addEventListener("click", () => {
      const dateInput = document.querySelector("#record-date");
      const providerInput = document.querySelector("#provider");
      if (dateInput.value && providerInput.value) {
        const query = new URLSearchParams({ date: dateInput.value, provider_id: providerInput.value });
        window.location.href = `/daily?${query.toString()}`;
      }
    });
  }

  document.querySelectorAll("[data-today-default]").forEach((input) => {
    if (!input.value) input.value = new Date().toISOString().slice(0, 10);
  });
});

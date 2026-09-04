function toggleReasonSections() {
    const select = document.getElementById("reason_id");
    if (!select) return;

    const option = select.options[select.selectedIndex];
    const map = {
        public: option?.dataset.public === "1",
        overcharging: option?.dataset.overcharging === "1",
        inspection: option?.dataset.inspection === "1",
    };

    document.querySelectorAll(".reason-section").forEach((section) => {
        const isVisible = map[section.dataset.section] === true;
        section.classList.toggle("d-none", !isVisible);
    });
}

document.addEventListener("DOMContentLoaded", () => {
    toggleReasonSections();
    document.getElementById("reason_id")?.addEventListener("change", toggleReasonSections);
});

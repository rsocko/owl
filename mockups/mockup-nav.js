(() => {
  const link = document.createElement("a");
  link.href = "../index.html";
  link.className = "owl-mockup-index-link";
  link.setAttribute("aria-label", "Return to the OWL mockup review index");
  link.innerHTML = `
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
      <path d="m15 18-6-6 6-6" />
      <path d="M9 12h10" />
    </svg>
    <span>All mockups</span>
  `;

  const style = document.createElement("style");
  style.textContent = `
    .owl-mockup-index-link {
      position: fixed;
      z-index: 9999;
      right: 16px;
      bottom: 16px;
      display: inline-flex;
      align-items: center;
      gap: 7px;
      min-height: 38px;
      padding: 0 13px;
      border: 1px solid #c9d0dc;
      border-radius: 10px;
      color: #1a1d2e;
      background: rgba(255, 255, 255, 0.96);
      box-shadow: 0 8px 24px rgba(26, 29, 46, 0.16);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 12px;
      font-weight: 700;
      line-height: 1;
      text-decoration: none;
      transition: background 160ms ease, border-color 160ms ease, transform 160ms ease;
    }

    .owl-mockup-index-link:hover {
      border-color: #2579b8;
      color: #1a1d2e;
      background: #f3f8fc;
      transform: translateY(-1px);
    }

    .owl-mockup-index-link:focus-visible {
      outline: 3px solid rgba(11, 111, 175, 0.28);
      outline-offset: 2px;
    }

    .owl-mockup-index-link svg {
      width: 15px;
      height: 15px;
    }

    @media (max-width: 560px) {
      .owl-mockup-index-link {
        right: 10px;
        bottom: 10px;
      }
    }

    @media (prefers-reduced-motion: reduce) {
      .owl-mockup-index-link {
        transition: none;
      }
    }
  `;

  document.head.append(style);
  document.body.append(link);
})();

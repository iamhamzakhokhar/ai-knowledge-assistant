(() => {
  const script = document.currentScript;
  const tagName = "aka-chat-widget";
  const defaultGreeting = "Welcome! Your knowledge assistant will live here.";
  const defaultBrandColor = "#2563eb";
  const template = document.createElement("template");

  // The template is static. All configurable text is assigned with textContent.
  template.innerHTML = `
    <style>
      :host {
        --aka-brand-color: ${defaultBrandColor};
        position: fixed;
        right: 20px;
        bottom: max(20px, env(safe-area-inset-bottom, 0px));
        z-index: 2147483000;
        display: block;
        color: #172033;
        color-scheme: light;
        font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        font-size: 16px;
        line-height: 1.5;
        text-align: left;
      }
      :host([hidden]), [hidden] { display: none !important; }
      *, *::before, *::after { box-sizing: border-box; }
      button { font: inherit; }
      button:focus-visible {
        outline: 3px solid #2563eb;
        outline-offset: 4px;
      }
      .widget { display: flex; flex-direction: column; align-items: flex-end; gap: 12px; }
      .panel {
        width: min(352px, calc(100vw - 40px));
        max-height: calc(100vh - 112px);
        max-height: calc(100dvh - 112px);
        overflow: auto;
        border: 1px solid #cbd5e1;
        border-top: 4px solid var(--aka-brand-color);
        border-radius: 20px;
        background: #fff;
        box-shadow: 0 12px 40px rgb(15 23 42 / 18%);
        overflow-wrap: anywhere;
      }
      .header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        padding: 16px 16px 12px 20px;
        border-bottom: 1px solid #e2e8f0;
      }
      h2 { margin: 0; font-size: 17px; line-height: 1.4; font-weight: 700; }
      .eyebrow { margin: 3px 0 0; color: #475569; font-size: 12px; }
      .close {
        display: grid;
        place-items: center;
        flex-shrink: 0;
        width: 44px;
        height: 44px;
        padding: 0;
        border: 1px solid #cbd5e1;
        border-radius: 12px;
        background: #fff;
        color: #172033;
        cursor: pointer;
      }
      .close:hover { background: #f1f5f9; }
      .body { padding: 22px 20px; }
      .greeting { margin: 0 0 20px; font-size: 18px; font-weight: 600; line-height: 1.5; }
      .notice { padding: 16px; border: 1px solid #cbd5e1; border-radius: 12px; background: #f8fafc; }
      .status { margin: 0; color: #172033; font-size: 14px; font-weight: 600; }
      .hint { margin: 8px 0 0; color: #475569; font-size: 14px; }
      .footer { margin: 18px 0 0; color: #475569; font-size: 12px; }
      .launcher {
        display: grid;
        place-items: center;
        width: 56px;
        height: 56px;
        padding: 0;
        border: 3px solid var(--aka-brand-color);
        border-radius: 50%;
        background: #172033;
        color: #fff;
        box-shadow: 0 4px 18px rgb(15 23 42 / 20%);
        cursor: pointer;
        transition: transform 140ms ease;
      }
      .launcher:hover { transform: translateY(-2px); }
      svg { display: block; width: 24px; height: 24px; pointer-events: none; }
      @media (max-width: 480px) {
        :host { right: 12px; bottom: max(12px, env(safe-area-inset-bottom, 0px)); }
        .panel { width: min(352px, calc(100vw - 24px)); }
      }
      @media (prefers-reduced-motion: reduce) {
        .launcher { transition: none; }
        .launcher:hover { transform: none; }
      }
    </style>
    <div class="widget">
      <section class="panel" id="aka-panel" role="dialog" aria-labelledby="aka-title" hidden>
        <header class="header">
          <div>
            <h2 id="aka-title">Knowledge assistant</h2>
            <p class="eyebrow">Setup preview</p>
          </div>
          <button class="close" type="button" aria-label="Close knowledge assistant">
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false">
              <path d="m6 6 12 12M18 6 6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
            </svg>
          </button>
        </header>
        <div class="body">
          <p class="greeting"></p>
          <div class="notice">
            <p class="status">Messaging is not connected yet.</p>
            <p class="hint">This setup preview shows how the assistant will look on your website.</p>
          </div>
          <p class="footer">You can close this preview and keep browsing.</p>
        </div>
      </section>
      <button class="launcher" type="button" aria-label="Open knowledge assistant" aria-expanded="false" aria-controls="aka-panel">
        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false">
          <path d="M20 11.5a8 8 0 0 1-8 8H5l-3 2v-10a8 8 0 0 1 8-8h2a8 8 0 0 1 8 8Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" />
          <path d="M7 10h8M7 14h5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
        </svg>
      </button>
    </div>
  `;

  class KnowledgeAssistantWidget extends HTMLElement {
    static get observedAttributes(): string[] {
      return ["greeting", "brand-color"];
    }

    private readonly panel: HTMLElement;
    private readonly launcher: HTMLButtonElement;
    private readonly closeButton: HTMLButtonElement;
    private readonly greeting: HTMLParagraphElement;
    private isOpen = false;

    constructor() {
      super();
      const shadow = this.attachShadow({ mode: "open" });
      shadow.append(template.content.cloneNode(true));
      const panel = shadow.querySelector<HTMLElement>(".panel");
      const launcher = shadow.querySelector<HTMLButtonElement>(".launcher");
      const closeButton = shadow.querySelector<HTMLButtonElement>(".close");
      const greeting = shadow.querySelector<HTMLParagraphElement>(".greeting");
      if (!panel || !launcher || !closeButton || !greeting) {
        throw new Error("Knowledge assistant widget template is incomplete.");
      }
      this.panel = panel;
      this.launcher = launcher;
      this.closeButton = closeButton;
      this.greeting = greeting;

      this.launcher.addEventListener("click", () => this.setOpen(!this.isOpen));
      this.closeButton.addEventListener("click", () => this.setOpen(false));
      shadow.addEventListener("keydown", (event: Event) => {
        if (event instanceof KeyboardEvent && event.key === "Escape" && this.isOpen) {
          event.preventDefault();
          event.stopPropagation();
          this.setOpen(false);
        }
      });
    }

    connectedCallback(): void {
      this.updateSettings();
    }

    attributeChangedCallback(): void {
      this.updateSettings();
    }

    private updateSettings(): void {
      const greeting = this.getAttribute("greeting")?.trim() || defaultGreeting;
      this.greeting.textContent = greeting.slice(0, 280);

      const candidate = this.getAttribute("brand-color")?.trim() || defaultBrandColor;
      const color =
        typeof CSS !== "undefined" && CSS.supports("color", candidate)
          ? candidate
          : defaultBrandColor;
      this.style.setProperty("--aka-brand-color", color);
    }

    private setOpen(open: boolean): void {
      this.isOpen = open;
      this.panel.hidden = !open;
      this.launcher.setAttribute("aria-expanded", String(open));
      this.launcher.setAttribute("aria-label", `${open ? "Close" : "Open"} knowledge assistant`);

      // This is a non-modal preview: opening never traps focus on the host page.
      if (open) {
        this.closeButton.focus({ preventScroll: true });
      } else {
        this.launcher.focus({ preventScroll: true });
      }
    }
  }

  if (!customElements.get(tagName)) {
    customElements.define(tagName, KnowledgeAssistantWidget);
  }

  if (script instanceof HTMLScriptElement && script.dataset.autoMount !== "false") {
    const mount = (): void => {
      // An existing custom element or repeated script tag must not add a duplicate.
      if (document.querySelector(tagName)) return;

      const widget = document.createElement(tagName);
      if (script.dataset.greeting !== undefined) {
        widget.setAttribute("greeting", script.dataset.greeting);
      }
      if (script.dataset.brandColor !== undefined) {
        widget.setAttribute("brand-color", script.dataset.brandColor);
      }
      document.body.append(widget);
    };

    if (document.body) {
      mount();
    } else {
      document.addEventListener("DOMContentLoaded", mount, { once: true });
    }
  }
})();

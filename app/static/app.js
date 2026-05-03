document.addEventListener("DOMContentLoaded", () => {
  const dashboard = document.querySelector("[data-dashboard]");
  const adminPage = document.querySelector("[data-admin-page]");
  const adminServersPage = document.querySelector("[data-admin-servers-page]");
  const adminJobsPage = document.querySelector("[data-admin-jobs-page]");
  const targetUserId = dashboard?.getAttribute("data-target-user-id");
  const isPreviewMode = dashboard?.getAttribute("data-preview-mode") === "1";

  const setStatus = (node, message, isError = false) => {
    if (!node) {
      return;
    }
    node.textContent = message;
    node.classList.toggle("is-error", isError);
  };

  const showToast = (title, message, type = "success") => {
    const stack = document.querySelector("[data-toast-stack]");
    if (!stack) {
      return;
    }
    const toast = document.createElement("div");
    toast.className = `toast ${type === "error" ? "is-error" : "is-success"}`;
    toast.innerHTML = `
      <span class="toast-icon">${type === "error" ? "!" : "✓"}</span>
      <span><strong class="toast-title"></strong><span class="toast-message"></span></span>
      <button class="toast-close" type="button" aria-label="Закрыть">×</button>
    `;
    toast.querySelector(".toast-title").textContent = title;
    toast.querySelector(".toast-message").textContent = message;
    toast.querySelector(".toast-close")?.addEventListener("click", () => toast.remove());
    stack.appendChild(toast);
    window.setTimeout(() => toast.remove(), 3200);
  };

  const showToastAfterReload = (title, message, type = "success") => {
    window.sessionStorage.setItem("nelomai:toast", JSON.stringify({ title, message, type }));
  };

  const pendingToast = window.sessionStorage.getItem("nelomai:toast");
  if (pendingToast) {
    window.sessionStorage.removeItem("nelomai:toast");
    try {
      const toast = JSON.parse(pendingToast);
      showToast(toast.title || "success", toast.message || "", toast.type || "success");
    } catch {
      // Ignore malformed transient UI state.
    }
  }

  const setActionBusy = (button, isBusy) => {
    if (!button) {
      return;
    }
    button.classList.toggle("is-action-busy", isBusy);
    button.disabled = isBusy;
  };

  const buildApiUrl = (path) => {
    const url = new URL(path, window.location.origin);
    if (isPreviewMode) {
      url.searchParams.set("preview", "1");
    }
    return url.toString();
  };

  const requestJson = async (path, options = {}) => {
    const response = await fetch(buildApiUrl(path), {
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
      ...options,
    });
    if (!response.ok) {
      let message = "Не удалось выполнить запрос";
      try {
        const data = await response.json();
        message = data.detail || message;
      } catch {
        // ignore
      }
      throw new Error(message);
    }
    if (response.status === 204) {
      return null;
    }
    return response.json();
  };

  const isValidIPv4FilterValue = (value) => {
    const parts = String(value || "").trim().split(".");
    return parts.length === 4 && parts.every((part) => {
      if (!/^\d+$/.test(part)) {
        return false;
      }
      const number = Number(part);
      return number >= 0 && number <= 255;
    });
  };

  const validateFilterPayload = (payload) => {
    if (payload.filter_type === "ip" && !isValidIPv4FilterValue(payload.value)) {
      throw new Error("IP filter must be in x.x.x.x format, each x from 0 to 255");
    }
  };

  const reloadDashboardOnTab = (tabName) => {
    if (dashboard) {
      const url = new URL(window.location.href);
      url.hash = tabName;
      window.history.replaceState(null, "", url.toString());
    }
    window.location.reload();
  };

  const filterTabForElement = (element) => {
    const panel = element?.closest("[data-tab-panel]");
    return panel?.getAttribute("data-tab-panel") || "filters";
  };

  const initTabs = () => {
    document.querySelectorAll("[data-tabs]").forEach((tabsRoot) => {
      const buttons = tabsRoot.querySelectorAll(":scope > .tab-list [data-tab-target]");
      const panels = tabsRoot.querySelectorAll(":scope > .tab-panel");
      const activateTab = (target) => {
        buttons.forEach((item) => {
          item.classList.toggle("active", item.getAttribute("data-tab-target") === target);
        });
        panels.forEach((panel) => {
          panel.classList.toggle("active", panel.getAttribute("data-tab-panel") === target);
        });
      };
      const hashTarget = window.location.hash.replace("#", "");
      if (hashTarget && Array.from(buttons).some((button) => button.getAttribute("data-tab-target") === hashTarget)) {
        activateTab(hashTarget);
      }
      buttons.forEach((button) => {
        button.addEventListener("click", () => {
          const target = button.getAttribute("data-tab-target");
          activateTab(target);
        });
      });
    });
  };

  initTabs();

  const extractServerSuffix = (value) => {
    const match = String(value || "").toLowerCase().match(/\b\d([a-z])\b/);
    return match ? match[1] : "";
  };

  const contactModal = document.querySelector("[data-contact-modal]");
  document.querySelector("[data-open-contact]")?.addEventListener("click", () => {
    if (!contactModal) {
      return;
    }
    contactModal.hidden = false;
  });
  document.querySelector("[data-close-contact]")?.addEventListener("click", () => {
    if (!contactModal) {
      return;
    }
    contactModal.hidden = true;
  });
  contactModal?.addEventListener("click", (event) => {
    if (event.target === contactModal) {
      contactModal.hidden = true;
    }
  });

  document.querySelectorAll("[data-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      const value = button.getAttribute("data-copy") || "";
      if (!value) {
        return;
      }
      await navigator.clipboard.writeText(value);
      const previous = button.textContent;
      button.textContent = "Скопировано";
      showToast("success", "Значение скопировано");
      window.setTimeout(() => {
        button.textContent = previous;
      }, 1200);
    });
  });

  const bindFilterSaveButtons = () => {
    document.querySelectorAll("[data-filter-save]").forEach((button) => {
      button.addEventListener("click", async () => {
        const row = button.closest("[data-filter-id]");
        const filterId = row?.getAttribute("data-filter-id");
        if (!row || !filterId) {
          return;
        }
        const payload = {};
        row.querySelectorAll("[data-filter-field]").forEach((field) => {
          payload[field.getAttribute("data-filter-field")] = field.value;
        });
        try {
          validateFilterPayload(payload);
          await requestJson(`/api/filters/${filterId}`, {
            method: "PATCH",
            body: JSON.stringify(payload),
          });
          showToastAfterReload("success", "Фильтр сохранён");
          reloadDashboardOnTab(filterTabForElement(button));
        } catch (error) {
          window.alert(error.message);
          showToast("error", error.message, "error");
        }
      });
    });

    document.querySelectorAll("[data-filter-delete]").forEach((button) => {
      button.addEventListener("click", async () => {
        const row = button.closest("[data-filter-id]");
        const filterId = row?.getAttribute("data-filter-id");
        if (!filterId) {
          return;
        }
        try {
          await requestJson(`/api/filters/${filterId}`, { method: "DELETE" });
          showToastAfterReload("success", "Фильтр удалён");
          reloadDashboardOnTab(filterTabForElement(button));
        } catch (error) {
          window.alert(error.message);
          showToast("error", error.message, "error");
        }
      });
    });
  };

  bindFilterSaveButtons();

  if (dashboard && targetUserId) {
    const disableInvalidInterfaceControls = () => {
      dashboard.querySelectorAll(".interface-section").forEach((section) => {
        const invalidBadge = section.querySelector(".availability.is-invalid");
        if (!invalidBadge) {
          return;
        }
        section
          .querySelectorAll(
            "[data-interface-toggle], [data-interface-peer-limit], [data-interface-route-mode], [data-interface-tak-server], [data-interface-exclusion-filters], [data-peer-toggle], [data-peer-block-filters], [data-peer-comment-input], [data-peer-comment-save], [data-peer-expiry-input], [data-peer-expiry-save], [data-peer-recreate], [data-peer-delete], [data-peer-link-copy], [data-peer-create]",
          )
          .forEach((control) => {
            control.setAttribute("disabled", "disabled");
            control.classList.add("is-disabled-control");
            if (!control.getAttribute("title")) {
              control.setAttribute("title", "Недействительный интерфейс недоступен для управления");
            }
          });

        section
          .querySelectorAll('a[href^="/api/peers/"], a[href^="/api/interfaces/"], a[href^="/downloads/auth/"]')
          .forEach((link) => {
            link.classList.add("is-disabled-control");
            link.setAttribute("aria-disabled", "true");
            if (!link.getAttribute("title")) {
              link.setAttribute("title", "Недействительный интерфейс недоступен для управления");
            }
            link.addEventListener("click", (event) => {
              event.preventDefault();
            });
          });
      });
    };

    disableInvalidInterfaceControls();

    const syncInterfaceRouteControls = (interfaceId, preferredRouteMode = null) => {
      const routeSelect = document.querySelector(`[data-interface-route-mode="${interfaceId}"]`);
      const takSelect = document.querySelector(`[data-interface-tak-server="${interfaceId}"]`);
      if (!routeSelect || !takSelect) {
        return;
      }
      const hasTakServer = Boolean(takSelect.value);
      const viaTakOption = routeSelect.querySelector('option[value="via_tak"]');
      if (viaTakOption) {
        viaTakOption.disabled = !hasTakServer;
      }
      if (!hasTakServer) {
        routeSelect.value = "standalone";
      } else if (preferredRouteMode && (preferredRouteMode === "standalone" || preferredRouteMode === "via_tak")) {
        routeSelect.value = preferredRouteMode;
      } else if (routeSelect.value !== "standalone" && routeSelect.value !== "via_tak") {
        routeSelect.value = "standalone";
      }
    };

    document.querySelectorAll("[data-interface-route-mode]").forEach((select) => {
      const interfaceId = select.getAttribute("data-interface-route-mode");
      if (interfaceId) {
        syncInterfaceRouteControls(interfaceId, select.value);
      }
    });

    const resolveInterfaceIdFromSection = (button) => {
      const section = button.closest(".interface-section");
      if (!section) {
        return null;
      }
      return (
        section.querySelector("[data-interface-toggle]")?.getAttribute("data-interface-toggle")
        || section.querySelector("[data-interface-route-mode]")?.getAttribute("data-interface-route-mode")
        || section.querySelector("[data-interface-tak-server]")?.getAttribute("data-interface-tak-server")
        || null
      );
    };

    dashboard.querySelectorAll(".interface-header-actions .primary-button.small:not([data-peer-create])").forEach((button) => {
      const interfaceId = resolveInterfaceIdFromSection(button);
      if (interfaceId) {
        button.setAttribute("data-peer-create", interfaceId);
      }
    });

    const peerCreateButtons = new Set(document.querySelectorAll("[data-peer-create]"));

    peerCreateButtons.forEach((button) => {
      button.addEventListener("click", async () => {
        const interfaceId = button.getAttribute("data-peer-create");
        if (!interfaceId) {
          return;
        }
        try {
          await requestJson(`/api/interfaces/${interfaceId}/peers`, { method: "POST" });
          showToastAfterReload("success", "Пир создан");
          window.setTimeout(() => window.location.reload(), 150);
        } catch (error) {
          window.alert(error.message);
          showToast("error", error.message, "error");
        }
      });
    });

    document.querySelectorAll("[data-peer-comment-save]").forEach((button) => {
      button.addEventListener("click", async () => {
        const peerId = button.getAttribute("data-peer-comment-save");
        const input = document.querySelector(`[data-peer-comment-input="${peerId}"]`);
        if (!peerId || !input) {
          return;
        }
        try {
          await requestJson(`/api/peers/${peerId}/comment`, {
            method: "PUT",
            body: JSON.stringify({ comment: input.value }),
          });
          showToastAfterReload("success", "Комментарий сохранён");
          window.setTimeout(() => window.location.reload(), 150);
        } catch (error) {
          window.alert(error.message);
          showToast("error", error.message, "error");
        }
      });
    });

    document.querySelectorAll("[data-peer-expiry-save]").forEach((button) => {
      button.addEventListener("click", async () => {
        const peerId = button.getAttribute("data-peer-expiry-save");
        const input = document.querySelector(`[data-peer-expiry-input="${peerId}"]`);
        if (!peerId || !input) {
          return;
        }
        try {
          await requestJson(`/api/peers/${peerId}/expires`, {
            method: "PUT",
            body: JSON.stringify({ expires_at: input.value ? new Date(input.value).toISOString() : null }),
          });
          showToastAfterReload("success", "Срок пира обновлён");
          window.setTimeout(() => window.location.reload(), 150);
        } catch (error) {
          window.alert(error.message);
          showToast("error", error.message, "error");
        }
      });
    });

    document.querySelectorAll("[data-user-expires-save]").forEach((button) => {
      button.addEventListener("click", async () => {
        const userId = button.getAttribute("data-user-expires-save");
        const input = document.querySelector(`[data-user-expires-input="${userId}"]`);
        const statusNode = document.querySelector("[data-user-expires-status]");
        if (!userId || !input) {
          return;
        }
        try {
          setStatus(statusNode, "Сохранение...");
          await requestJson(`/api/admin/users/${userId}/expires`, {
            method: "PUT",
            body: JSON.stringify({ expires_at: input.value ? new Date(input.value).toISOString() : null }),
          });
          setStatus(statusNode, "Срок обновлён");
          showToast("success", "Срок действия обновлён");
        } catch (error) {
          setStatus(statusNode, error.message, true);
          showToast("error", error.message, "error");
        }
      });
    });

    document.querySelectorAll("[data-peer-link-copy]").forEach((button) => {
      button.addEventListener("click", async () => {
        const peerId = button.getAttribute("data-peer-link-copy");
        if (!peerId) {
          return;
        }
        try {
          setActionBusy(button, true);
          const data = await requestJson(`/api/peers/${peerId}/download-link`, {
            method: "POST",
          });
          await navigator.clipboard.writeText(String(data.url || ""));
          const previous = button.textContent;
          button.textContent = "Ссылка скопирована";
          showToast("success", "Ссылка на пир скопирована");
          window.setTimeout(() => {
            button.textContent = previous;
            setActionBusy(button, false);
          }, 1200);
        } catch (error) {
          setActionBusy(button, false);
          window.alert(error.message);
          showToast("error", error.message, "error");
        }
      });
    });

    document.querySelectorAll("[data-peer-toggle]").forEach((checkbox) => {
      checkbox.addEventListener("change", async () => {
        const peerId = checkbox.getAttribute("data-peer-toggle");
        if (!peerId) {
          return;
        }
        try {
          await requestJson(`/api/peers/${peerId}/toggle`, {
            method: "POST",
          });
          showToastAfterReload("success", "Статус пира обновлён");
          window.setTimeout(() => window.location.reload(), 150);
        } catch (error) {
          checkbox.checked = !checkbox.checked;
          window.alert(error.message);
          showToast("error", error.message, "error");
        }
      });
    });

    document.querySelectorAll("[data-peer-block-filters]").forEach((checkbox) => {
      checkbox.addEventListener("change", async () => {
        const peerId = checkbox.getAttribute("data-peer-block-filters");
        if (!peerId) {
          return;
        }
        try {
          await requestJson(`/api/admin/peers/${peerId}/block-filters`, {
            method: "PUT",
            body: JSON.stringify({ enabled: checkbox.checked }),
          });
          showToastAfterReload("success", "Настройка блоков обновлена");
          window.setTimeout(() => window.location.reload(), 150);
        } catch (error) {
          checkbox.checked = !checkbox.checked;
          window.alert(error.message);
          showToast("error", error.message, "error");
        }
      });
    });

    document.querySelectorAll("[data-peer-recreate]").forEach((button) => {
      button.addEventListener("click", async () => {
        const peerId = button.getAttribute("data-peer-recreate");
        if (!peerId) {
          return;
        }
        try {
          await requestJson(`/api/peers/${peerId}/recreate`, {
            method: "POST",
          });
          showToastAfterReload("success", "Пир пересоздан");
          window.setTimeout(() => window.location.reload(), 150);
        } catch (error) {
          window.alert(error.message);
          showToast("error", error.message, "error");
        }
      });
    });

    document.querySelectorAll("[data-peer-delete]").forEach((button) => {
      button.addEventListener("click", async () => {
        const peerId = button.getAttribute("data-peer-delete");
        if (!peerId) {
          return;
        }
        try {
          await requestJson(`/api/peers/${peerId}`, {
            method: "DELETE",
          });
          showToastAfterReload("success", "Пир удалён");
          window.setTimeout(() => window.location.reload(), 150);
        } catch (error) {
          window.alert(error.message);
          showToast("error", error.message, "error");
        }
      });
    });

    document.querySelectorAll("[data-assign-interface]").forEach((button) => {
      button.addEventListener("click", async () => {
        const interfaceId = button.getAttribute("data-assign-interface");
        if (!interfaceId || !targetUserId) {
          return;
        }
        try {
          await requestJson(`/api/admin/users/${targetUserId}/assign-interface/${interfaceId}`, { method: "POST" });
          showToastAfterReload("success", "Интерфейс привязан");
          window.setTimeout(() => window.location.reload(), 150);
        } catch (error) {
          window.alert(error.message);
          showToast("error", error.message, "error");
        }
      });
    });

    document.querySelectorAll("[data-interface-detach]").forEach((button) => {
      button.addEventListener("click", async () => {
        const interfaceId = button.getAttribute("data-interface-detach");
        if (!interfaceId || !targetUserId) {
          return;
        }
        try {
          await requestJson(`/api/admin/users/${targetUserId}/detach-interface/${interfaceId}`, { method: "POST" });
          showToastAfterReload("success", "Интерфейс отвязан");
          window.setTimeout(() => window.location.reload(), 150);
        } catch (error) {
          window.alert(error.message);
          showToast("error", error.message, "error");
        }
      });
    });

    document.querySelectorAll("[data-interface-toggle]").forEach((checkbox) => {
      checkbox.addEventListener("change", async () => {
        const interfaceId = checkbox.getAttribute("data-interface-toggle");
        if (!interfaceId) {
          return;
        }
        try {
          await requestJson(`/api/admin/interfaces/${interfaceId}/toggle`, { method: "POST" });
          showToastAfterReload("success", "Статус интерфейса обновлён");
          window.setTimeout(() => window.location.reload(), 150);
        } catch (error) {
          checkbox.checked = !checkbox.checked;
          window.alert(error.message);
          showToast("error", error.message, "error");
        }
      });
    });

    document.querySelectorAll("[data-interface-peer-limit]").forEach((select) => {
      select.addEventListener("change", async () => {
        const interfaceId = select.getAttribute("data-interface-peer-limit");
        if (!interfaceId) {
          return;
        }
        try {
          await requestJson(`/api/admin/interfaces/${interfaceId}/peer-limit`, {
            method: "PUT",
            body: JSON.stringify({ peer_limit: Number(select.value) }),
          });
          showToastAfterReload("success", "Лимит пиров обновлён");
          window.setTimeout(() => window.location.reload(), 150);
        } catch (error) {
          window.alert(error.message);
          showToastAfterReload("error", error.message, "error");
          window.setTimeout(() => window.location.reload(), 150);
        }
      });
    });

    document.querySelectorAll("[data-interface-route-mode]").forEach((select) => {
      select.addEventListener("change", async () => {
        const interfaceId = select.getAttribute("data-interface-route-mode");
        if (!interfaceId) {
          return;
        }
        const takSelect = document.querySelector(`[data-interface-tak-server="${interfaceId}"]`);
        if (select.value === "via_tak" && takSelect && !takSelect.value) {
          select.value = "standalone";
          window.alert("Для режима via_tak сначала выберите Tak endpoint.");
          return;
        }
        const previousValue = select.getAttribute("data-current-value") || select.value;
        try {
          const response = await requestJson(`/api/admin/interfaces/${interfaceId}/route-mode`, {
            method: "PUT",
            body: JSON.stringify({ route_mode: String(select.value || "standalone") }),
          });
          const nextMode = String(response.route_mode || "standalone");
          select.setAttribute("data-current-value", nextMode);
          syncInterfaceRouteControls(interfaceId, nextMode);
          showToast("success", "Маршрут интерфейса обновлён");
        } catch (error) {
          select.value = previousValue;
          window.alert(error.message);
          showToast("error", error.message, "error");
          syncInterfaceRouteControls(interfaceId, previousValue);
        }
      });
      select.setAttribute("data-current-value", select.value);
    });

    document.querySelectorAll("[data-interface-tak-server]").forEach((select) => {
      select.addEventListener("change", async () => {
        const interfaceId = select.getAttribute("data-interface-tak-server");
        if (!interfaceId) {
          return;
        }
        const previousTakServerId = select.getAttribute("data-current-value") || "";
        const routeSelect = document.querySelector(`[data-interface-route-mode="${interfaceId}"]`);
        const previousRouteMode = routeSelect?.getAttribute("data-current-value") || routeSelect?.value || "standalone";
        syncInterfaceRouteControls(interfaceId, routeSelect?.value || "standalone");
        try {
          const response = await requestJson(`/api/admin/interfaces/${interfaceId}/tak-server`, {
            method: "PUT",
            body: JSON.stringify({
              tak_server_id: select.value ? Number(select.value) : null,
            }),
          });
          const nextTakServerId = response.tak_server_id === null ? "" : String(response.tak_server_id);
          const nextRouteMode = String(response.route_mode || "standalone");
          select.value = nextTakServerId;
          select.setAttribute("data-current-value", nextTakServerId);
          if (routeSelect) {
            routeSelect.setAttribute("data-current-value", nextRouteMode);
          }
          syncInterfaceRouteControls(interfaceId, nextRouteMode);
          showToast("success", "Endpoint интерфейса обновлён");
        } catch (error) {
          select.value = previousTakServerId;
          select.setAttribute("data-current-value", previousTakServerId);
          if (routeSelect) {
            routeSelect.value = previousRouteMode;
            routeSelect.setAttribute("data-current-value", previousRouteMode);
          }
          window.alert(error.message);
          showToast("error", error.message, "error");
          syncInterfaceRouteControls(interfaceId, previousRouteMode);
        }
      });
      select.setAttribute("data-current-value", select.value);
    });

    document.querySelectorAll("[data-interface-exclusion-filters]").forEach((checkbox) => {
      checkbox.addEventListener("change", async () => {
        const interfaceId = checkbox.getAttribute("data-interface-exclusion-filters");
        if (!interfaceId) {
          return;
        }
        try {
          const response = await requestJson(`/api/admin/interfaces/${interfaceId}/exclusion-filters`, {
            method: "PUT",
            body: JSON.stringify({ enabled: checkbox.checked }),
          });
          checkbox.checked = Boolean(response.enabled);
          showToastAfterReload("success", "Фильтрация интерфейса обновлена");
          window.setTimeout(() => window.location.reload(), 150);
        } catch (error) {
          checkbox.checked = !checkbox.checked;
          window.alert(error.message);
          showToast("error", error.message, "error");
        }
      });
    });

    const resourceForm = document.querySelector("[data-resource-form]");
    if (resourceForm) {
      const statusNode = resourceForm.querySelector("[data-resource-status]");
      resourceForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        const formData = new FormData(resourceForm);
        const payload = Object.fromEntries(formData.entries());
        try {
          setStatus(statusNode, "Сохраняем...");
          await requestJson(`/api/users/${targetUserId}/resources`, {
            method: "PUT",
            body: JSON.stringify(payload),
          });
          setStatus(statusNode, "Сохранено");
          showToastAfterReload("success", "Прочие ресурсы сохранены");
          window.setTimeout(() => window.location.reload(), 400);
        } catch (error) {
          setStatus(statusNode, error.message, true);
          showToast("error", error.message, "error");
        }
      });

      const clearButton = resourceForm.querySelector("[data-resource-clear]");
      clearButton?.addEventListener("click", async () => {
        try {
          setStatus(statusNode, "Очищаем...");
          await requestJson(`/api/users/${targetUserId}/resources`, { method: "DELETE" });
          setStatus(statusNode, "Очищено");
          showToastAfterReload("success", "Прочие ресурсы очищены");
          window.setTimeout(() => window.location.reload(), 400);
        } catch (error) {
          setStatus(statusNode, error.message, true);
          showToast("error", error.message, "error");
        }
      });
    }

    document.querySelectorAll("[data-filter-create-form]").forEach((form) => {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const statusNode = form.querySelector("[data-filter-status]");
        const formData = new FormData(form);
        const payload = {
          ...Object.fromEntries(formData.entries()),
          scope: form.getAttribute("data-filter-scope"),
          kind: form.getAttribute("data-filter-kind") || "exclusion",
          is_active: true,
        };
        try {
          setStatus(statusNode, "Добавляем...");
          validateFilterPayload(payload);
          await requestJson(`/api/users/${targetUserId}/filters`, {
            method: "POST",
            body: JSON.stringify(payload),
          });
          setStatus(statusNode, "Добавлено");
          showToastAfterReload("success", "Фильтр добавлен");
          window.setTimeout(() => reloadDashboardOnTab(filterTabForElement(form)), 400);
        } catch (error) {
          setStatus(statusNode, error.message, true);
          showToast("error", error.message, "error");
        }
      });
    });
  }

  if (adminPage) {
    const interfaceModal = document.querySelector("[data-interface-modal]");
    const interfaceCreateForm = document.querySelector("[data-interface-create-form]");
    const interfaceTicSelect = interfaceCreateForm?.querySelector('select[name="tic_server_id"]');
    const interfaceTakSelect = interfaceCreateForm?.querySelector('select[name="tak_server_id"]');
    const interfaceSteps = Array.from(interfaceCreateForm?.querySelectorAll("[data-interface-step]") || []);
    let interfaceStepIndex = 0;

    const showInterfaceStep = (index) => {
      interfaceStepIndex = index;
      interfaceSteps.forEach((step, stepIndex) => {
        step.hidden = stepIndex !== index;
      });
    };

    const syncTakOptions = () => {
      if (!interfaceTicSelect || !interfaceTakSelect) {
        return;
      }
      const ticSuffix = extractServerSuffix(interfaceTicSelect.selectedOptions[0]?.textContent || "");
      let hasValidSelection = false;
      interfaceTakSelect.querySelectorAll("option").forEach((option) => {
        if (!option.value) {
          option.hidden = false;
          option.disabled = false;
          hasValidSelection = hasValidSelection || option.selected;
          return;
        }
        const isAllowed = extractServerSuffix(option.textContent || "") === ticSuffix;
        option.hidden = !isAllowed;
        option.disabled = !isAllowed;
        if (!isAllowed && option.selected) {
          option.selected = false;
        }
        if (isAllowed && option.selected) {
          hasValidSelection = true;
        }
      });
      if (!hasValidSelection) {
        interfaceTakSelect.value = "";
      }
    };

    document.querySelector("[data-open-interface-modal]")?.addEventListener("click", () => {
      if (!interfaceModal) {
        return;
      }
      interfaceCreateForm?.reset();
      syncTakOptions();
      showInterfaceStep(0);
      setStatus(interfaceCreateForm?.querySelector("[data-interface-status]"), "");
      interfaceModal.hidden = false;
    });
    document.querySelector("[data-close-interface-modal]")?.addEventListener("click", () => {
      if (!interfaceModal) {
        return;
      }
      interfaceModal.hidden = true;
    });
    interfaceModal?.addEventListener("click", (event) => {
      if (event.target === interfaceModal) {
        interfaceModal.hidden = true;
      }
    });
    interfaceTicSelect?.addEventListener("change", syncTakOptions);
    syncTakOptions();
    showInterfaceStep(0);

    document.querySelector("[data-interface-back]")?.addEventListener("click", () => {
      showInterfaceStep(0);
    });
    document.querySelector("[data-interface-next]")?.addEventListener("click", async (event) => {
      event.preventDefault();
      event.stopImmediatePropagation();
      const nextButton = event.currentTarget;
      const statusNode = interfaceCreateForm?.querySelector("[data-interface-status]");
      const nameInput = interfaceCreateForm?.querySelector('input[name="name"]');
      const listenPortInput = interfaceCreateForm?.querySelector('input[name="listen_port"]');
      const addressInput = interfaceCreateForm?.querySelector('input[name="address_v4"]');
      if (!interfaceTicSelect?.value || !nameInput?.value.trim()) {
        window.alert("???????? Tic ?????? ? ??????? ???????? ??????????.");
        return;
      }
      try {
        setActionBusy(nextButton, true);
        setStatus(statusNode, "??????????? ????????? ???? ? ?????...");
        const allocation = await requestJson("/api/admin/interfaces/prepare", {
          method: "POST",
          body: JSON.stringify({
            name: String(nameInput.value || "").trim(),
            tic_server_id: Number(interfaceTicSelect.value || 0),
            tak_server_id: interfaceTakSelect?.value ? Number(interfaceTakSelect.value) : null,
          }),
        });
        if (listenPortInput) {
          listenPortInput.value = String(allocation.listen_port || "");
        }
        if (addressInput) {
          addressInput.value = String(allocation.address_v4 || "");
        }
        setStatus(statusNode, "");
        showInterfaceStep(1);
      } catch (error) {
        setStatus(statusNode, error.message, true);
        showToast("??????", error.message, "error");
      } finally {
        setActionBusy(nextButton, false);
      }
    }, true);

    const basicSettingsForm = document.querySelector("[data-basic-settings-form]");
    basicSettingsForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const statusNode = basicSettingsForm.querySelector("[data-basic-settings-status]");
      const submitButton = basicSettingsForm.querySelector('button[type="submit"]');
      const formData = new FormData(basicSettingsForm);
      const payload = {
        dns_server: String(formData.get("dns_server") || ""),
        mtu: Number(formData.get("mtu") || 0),
        keepalive: Number(formData.get("keepalive") || 0),
        exclusion_filters_enabled: formData.get("exclusion_filters_enabled") === "1",
        block_filters_enabled: formData.get("block_filters_enabled") === "1",
        admin_telegram_url: String(formData.get("admin_telegram_url") || ""),
        admin_vk_url: String(formData.get("admin_vk_url") || ""),
        admin_email_url: String(formData.get("admin_email_url") || ""),
        admin_group_url: String(formData.get("admin_group_url") || ""),
      };
      try {
        setActionBusy(submitButton, true);
        setStatus(statusNode, "Сохраняем...");
        await requestJson("/api/admin/settings/basic", {
          method: "PUT",
          body: JSON.stringify(payload),
        });
        setStatus(statusNode, "Сохранено");
        showToast("success", "Настройки сохранены");
      } catch (error) {
        setStatus(statusNode, error.message, true);
        showToast("error", error.message, "error");
      } finally {
        setActionBusy(submitButton, false);
      }
    });

    const updateSettingsForm = document.querySelector("[data-update-settings-form]");
    updateSettingsForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const statusNode = updateSettingsForm.querySelector("[data-update-settings-status]");
      const submitButton = updateSettingsForm.querySelector('button[type="submit"]');
      const formData = new FormData(updateSettingsForm);
      const payload = {
        nelomai_git_repo: String(formData.get("nelomai_git_repo") || ""),
      };
      try {
        setActionBusy(submitButton, true);
        setStatus(statusNode, "Сохраняем...");
        await requestJson("/api/admin/settings/updates", {
          method: "PUT",
          body: JSON.stringify(payload),
        });
        setStatus(statusNode, "Сохранено");
        showToast("success", "Git-настройки сохранены");
      } catch (error) {
        setStatus(statusNode, error.message, true);
        showToast("error", error.message, "error");
      } finally {
        setActionBusy(submitButton, false);
      }
    });

    const updateCheckButton = document.querySelector("[data-panel-update-check]");
    updateCheckButton?.addEventListener("click", async () => {
      const statusNode = document.querySelector("[data-update-settings-status]");
      const resultNode = document.querySelector("[data-panel-update-result]");
      try {
        setActionBusy(updateCheckButton, true);
        setStatus(statusNode, "Проверяем обновления...");
        const result = await requestJson("/api/admin/updates/check");
        if (resultNode) {
          resultNode.hidden = false;
          const latest = result.latest_version || "не найдена";
          const message = result.update_available
            ? `Доступна новая версия: ${latest}. Текущая версия: ${result.current_version}.`
            : `Обновлений нет. Текущая версия: ${result.current_version}. Последняя версия: ${latest}.`;
          resultNode.textContent = result.message === "GitHub repository is not configured"
            ? "Укажите Git репозиторий Nelomai и сохраните настройки, затем повторите проверку."
            : message;
          resultNode.classList.toggle("is-warning", Boolean(result.update_available));
        }
        setStatus(statusNode, "Проверка завершена");
        showToast(result.update_available ? "success" : "success", result.update_available ? "Доступно обновление панели" : "Панель актуальна");
      } catch (error) {
        setStatus(statusNode, error.message, true);
        showToast("error", error.message, "error");
      } finally {
        setActionBusy(updateCheckButton, false);
      }
    });

    const agentUpdatesResult = document.querySelector("[data-agent-updates-result]");
    const agentUpdatesStatus = document.querySelector("[data-agent-updates-status]");
    const agentUpdateStatusLabel = (server) => {
      if (server.status === "repo_missing") {
        return "repo не задан";
      }
      if (server.status === "excluded") {
        return "исключён";
      }
      if (server.status === "legacy") {
        return "legacy";
      }
      if (server.status === "error") {
        return "ошибка";
      }
      if (server.update_available) {
        return "доступно";
      }
      if (server.status === "updated") {
        return "обновлён";
      }
      return server.status || "unknown";
    };

    const agentUpdateBadgeClass = (server) => {
      if (server.status === "error") {
        return "is-offline";
      }
      if (server.status === "repo_missing" || server.status === "excluded" || server.status === "legacy" || server.update_available) {
        return "is-invalid";
      }
      return "is-online";
    };

    const renderAgentUpdateResults = (servers) => {
      if (!agentUpdatesResult) {
        return;
      }
      agentUpdatesResult.innerHTML = "";
      if (!servers || !servers.length) {
        const empty = document.createElement("p");
        empty.className = "muted-note";
        empty.textContent = "Серверов для проверки пока нет.";
        agentUpdatesResult.appendChild(empty);
        return;
      }
      servers.forEach((server) => {
        const row = document.createElement("div");
        row.className = `agent-update-row ${server.status === "error" ? "is-error" : ""}`;
        const latest = server.latest_version || "неизвестно";
        const current = server.current_version || "неизвестно";
        const contract = server.is_legacy
          ? "legacy agent"
          : `contract: ${server.contract_version || "неизвестно"}`;
        const capabilities = Array.isArray(server.capabilities) && server.capabilities.length
          ? ` Возможности: ${server.capabilities.join(", ")}.`
          : "";
        row.innerHTML = `
          <div>
            <strong></strong>
            <p></p>
            <div class="backup-conflict-actions" hidden></div>
          </div>
          <span class="availability"></span>
          <button class="ghost-button small" type="button">Обновить</button>
        `;
        row.querySelector("strong").textContent = `${server.name} · ${String(server.server_type).toUpperCase()}`;
        row.querySelector("p").textContent = `${server.message}. Текущая: ${current}. Последняя: ${latest}. ${contract}.${capabilities}`;
        const badge = row.querySelector(".availability");
        badge.textContent = agentUpdateStatusLabel(server);
        badge.classList.add(agentUpdateBadgeClass(server));
        const button = row.querySelector("button");
        button.setAttribute("data-agent-update-server", String(server.server_id));
        button.disabled = server.status === "repo_missing" || server.status === "excluded";
        agentUpdatesResult.appendChild(row);
      });
    };

    document.querySelector("[data-agent-updates-check]")?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      try {
        setActionBusy(button, true);
        setStatus(agentUpdatesStatus, "Проверяем серверы...");
        const result = await requestJson("/api/admin/agent-updates/check");
        renderAgentUpdateResults(result.servers || []);
        setStatus(agentUpdatesStatus, "Проверка завершена");
        showToast("success", "Проверка агентов завершена");
      } catch (error) {
        setStatus(agentUpdatesStatus, error.message, true);
        showToast("error", error.message, "error");
      } finally {
        setActionBusy(button, false);
      }
    });

    document.querySelector("[data-agent-updates-apply-all]")?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      try {
        setActionBusy(button, true);
        setStatus(agentUpdatesStatus, "Запускаем обновление на всех серверах...");
        const result = await requestJson("/api/admin/agent-updates/apply", {
          method: "POST",
          body: JSON.stringify({ server_id: null }),
        });
        renderAgentUpdateResults(result.servers || []);
        setStatus(agentUpdatesStatus, "Обновление завершено");
        showToast("success", "Команда обновления агентов выполнена");
      } catch (error) {
        setStatus(agentUpdatesStatus, error.message, true);
        showToast("error", error.message, "error");
      } finally {
        setActionBusy(button, false);
      }
    });

    agentUpdatesResult?.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-agent-update-server]");
      const serverId = button?.getAttribute("data-agent-update-server");
      if (!button || !serverId) {
        return;
      }
      try {
        setActionBusy(button, true);
        setStatus(agentUpdatesStatus, "Обновляем выбранный сервер...");
        const result = await requestJson("/api/admin/agent-updates/apply", {
          method: "POST",
          body: JSON.stringify({ server_id: Number(serverId) }),
        });
        renderAgentUpdateResults(result.servers || []);
        setStatus(agentUpdatesStatus, "Сервер обновлён");
        showToast("success", "Команда обновления агента выполнена");
      } catch (error) {
        setStatus(agentUpdatesStatus, error.message, true);
        showToast("error", error.message, "error");
      } finally {
        setActionBusy(button, false);
      }
    });

    const backupSettingsForm = document.querySelector("[data-backup-settings-form]");
    backupSettingsForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const statusNode = backupSettingsForm.querySelector("[data-backup-settings-status]");
      const submitButton = backupSettingsForm.querySelector('button[type="submit"]');
      const formData = new FormData(backupSettingsForm);
      const payload = {
        backups_enabled: formData.get("backups_enabled") === "1",
        backup_frequency: String(formData.get("backup_frequency") || "daily"),
        backup_time: String(formData.get("backup_time") || "03:00"),
        backup_retention_days: Number(formData.get("backup_retention_days") || 30),
        backup_storage_path: String(formData.get("backup_storage_path") || ".tmp/backups"),
        server_backup_retention_days: Number(formData.get("server_backup_retention_days") || 90),
        server_backup_size_limit_mb: Number(formData.get("server_backup_size_limit_mb") || 5120),
        server_backup_monthly_retention_days: Number(formData.get("server_backup_monthly_retention_days") || 365),
        server_backup_monthly_size_limit_mb: Number(formData.get("server_backup_monthly_size_limit_mb") || 3072),
        backup_remote_storage_server_id: formData.get("backup_remote_storage_server_id")
          ? Number(formData.get("backup_remote_storage_server_id"))
          : null,
      };
      try {
        setActionBusy(submitButton, true);
        setStatus(statusNode, "Сохраняем...");
        await requestJson("/api/admin/settings/backups", {
          method: "PUT",
          body: JSON.stringify(payload),
        });
        setStatus(statusNode, "Сохранено");
        showToast("success", "Настройки бэкапов сохранены");
      } catch (error) {
        setStatus(statusNode, error.message, true);
        showToast("error", error.message, "error");
      } finally {
        setActionBusy(submitButton, false);
      }
    });

    const peerLinksStatus = document.querySelector("[data-peer-links-status]");
    document.querySelectorAll("[data-peer-link-revoke]").forEach((button) => {
      button.addEventListener("click", async () => {
        const linkId = button.getAttribute("data-peer-link-revoke");
        if (!linkId) {
          return;
        }
        try {
          setActionBusy(button, true);
          setStatus(peerLinksStatus, "Отзываем ссылку...");
          await requestJson(`/api/admin/peer-download-links/${linkId}`, { method: "DELETE" });
          showToastAfterReload("success", "Ссылка отозвана");
          window.setTimeout(() => window.location.reload(), 250);
        } catch (error) {
          setActionBusy(button, false);
          setStatus(peerLinksStatus, error.message, true);
          showToast("error", error.message, "error");
        }
      });
    });

    const revokePeerLinksBulk = async (button, lifetimeOnly) => {
      if (!button) {
        return;
      }
      const message = lifetimeOnly
        ? "Отозвать все бессрочные ссылки?"
        : "Отозвать все активные ссылки на скачивание пиров?";
      if (!window.confirm(message)) {
        return;
      }
      try {
        setActionBusy(button, true);
        setStatus(peerLinksStatus, "Отзываем ссылки...");
        const result = await requestJson(`/api/admin/peer-download-links/revoke-all?lifetime_only=${lifetimeOnly ? "true" : "false"}`, {
          method: "POST",
        });
        showToastAfterReload("success", `Отозвано ссылок: ${result.revoked || 0}`);
        window.setTimeout(() => window.location.reload(), 250);
      } catch (error) {
        setActionBusy(button, false);
        setStatus(peerLinksStatus, error.message, true);
        showToast("error", error.message, "error");
      }
    };

    document.querySelector("[data-peer-links-revoke-all]")?.addEventListener("click", (event) => {
      revokePeerLinksBulk(event.currentTarget, false);
    });
    document.querySelector("[data-peer-links-revoke-lifetime]")?.addEventListener("click", (event) => {
      revokePeerLinksBulk(event.currentTarget, true);
    });

    const backupCreateForm = document.querySelector("[data-backup-create-form]");
    backupCreateForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const statusNode = backupCreateForm.querySelector("[data-backup-create-status]");
      const submitButton = backupCreateForm.querySelector('button[type="submit"]');
      const formData = new FormData(backupCreateForm);
      try {
        setActionBusy(submitButton, true);
        setStatus(statusNode, "Создаём бэкап...");
        await requestJson("/api/admin/backups", {
          method: "POST",
          body: JSON.stringify({ backup_type: String(formData.get("backup_type") || "users") }),
        });
        showToastAfterReload("success", "Бэкап создан");
        window.setTimeout(() => window.location.reload(), 250);
      } catch (error) {
        setStatus(statusNode, error.message, true);
        showToast("error", error.message, "error");
      } finally {
        setActionBusy(submitButton, false);
      }
    });

    const backupScheduledRunButton = document.querySelector("[data-backup-scheduled-run-now]");
    backupScheduledRunButton?.addEventListener("click", async () => {
      const statusNode = document.querySelector("[data-backup-scheduled-status]");
      try {
        setActionBusy(backupScheduledRunButton, true);
        setStatus(statusNode, "Создаём плановый full backup...");
        await requestJson("/api/admin/backups/scheduled/run-now", { method: "POST" });
        showToastAfterReload("success", "Плановый бэкап создан");
        window.setTimeout(() => window.location.reload(), 250);
      } catch (error) {
        setStatus(statusNode, error.message, true);
        showToast("error", error.message, "error");
      } finally {
        setActionBusy(backupScheduledRunButton, false);
      }
    });

    const backupVerifyButton = document.querySelector("[data-backup-verify-server-copies]");
    backupVerifyButton?.addEventListener("click", async () => {
      const statusNode = document.querySelector("[data-backup-scheduled-status]");
      const resultNode = document.querySelector("[data-backup-verify-result]");
      try {
        setActionBusy(backupVerifyButton, true);
        setStatus(statusNode, "Проверяем свежий full backup...");
        const result = await requestJson("/api/admin/backups/latest-full/verify-server-copies", { method: "POST" });
        if (resultNode) {
          resultNode.hidden = false;
          resultNode.innerHTML = "";
          const title = document.createElement("p");
          title.className = "muted-note";
          title.textContent = `Backup ${result.filename}: ${result.status}`;
          resultNode.appendChild(title);
          (result.items || []).forEach((item) => {
            const row = document.createElement("div");
            row.className = "backup-verify-row";
            row.innerHTML = `
              <strong></strong>
              <span class="availability"></span>
              <p></p>
            `;
            row.querySelector("strong").textContent = `${item.server_name} · ${String(item.server_type).toUpperCase()}`;
            const badge = row.querySelector(".availability");
            badge.textContent = item.status;
            badge.classList.add(item.status === "matched" ? "is-online" : "is-invalid");
            row.querySelector("p").textContent = item.message;
            resultNode.appendChild(row);
          });
        }
        setStatus(statusNode, "Проверка завершена");
        showToast("success", "Проверка свежего full backup завершена");
      } catch (error) {
        setStatus(statusNode, error.message, true);
        showToast("error", error.message, "error");
      } finally {
        setActionBusy(backupVerifyButton, false);
      }
    });

    const backupCleanupServersButton = document.querySelector("[data-backup-cleanup-servers]");
    backupCleanupServersButton?.addEventListener("click", async () => {
      const statusNode = document.querySelector("[data-backup-scheduled-status]");
      if (!window.confirm("Очистить бэкапы на Tic/Tak серверах, оставив только последние копии? Бэкапы панели и удаленного хранилища не будут затронуты.")) {
        return;
      }
      try {
        setActionBusy(backupCleanupServersButton, true);
        setStatus(statusNode, "Очищаем бэкапы на серверах...");
        const result = await requestJson("/api/admin/backups/server-copies/cleanup", { method: "POST" });
        const completed = (result.items || []).filter((item) => item.status === "completed").length;
        const message = `Очистка серверов: ${completed}/${(result.items || []).length}`;
        setStatus(statusNode, message);
        showToast(result.status === "completed" ? "success" : "error", message, result.status === "completed" ? "success" : "warning");
      } catch (error) {
        setStatus(statusNode, error.message, true);
        showToast("error", error.message, "error");
      } finally {
        setActionBusy(backupCleanupServersButton, false);
      }
    });

    const backupDeletePanelAllButton = document.querySelector("[data-backup-delete-panel-all]");
    backupDeletePanelAllButton?.addEventListener("click", async () => {
      const statusNode = document.querySelector("[data-backup-scheduled-status]");
      if (!window.confirm("Удалить все бэкапы панели, кроме последнего? Бэкапы на серверах не будут затронуты.")) {
        return;
      }
      try {
        setActionBusy(backupDeletePanelAllButton, true);
        setStatus(statusNode, "Удаляем бэкапы панели...");
        const result = await requestJson("/api/admin/backups/delete-all-except-latest", { method: "POST" });
        const message = `Удалено: ${result.deleted_count || 0}, освобождено: ${result.freed_size_label || "0 КБ"}`;
        setStatus(statusNode, message);
        showToastAfterReload("success", message);
        window.setTimeout(() => window.location.reload(), 350);
      } catch (error) {
        setStatus(statusNode, error.message, true);
        showToast("error", error.message, "error");
      } finally {
        setActionBusy(backupDeletePanelAllButton, false);
      }
    });

    const backupTypeFilter = document.querySelector("[data-backup-type-filter]");
    backupTypeFilter?.addEventListener("change", () => {
      const selectedType = backupTypeFilter.value || "all";
      document.querySelectorAll("[data-backup-type]").forEach((row) => {
        row.hidden = selectedType !== "all" && row.getAttribute("data-backup-type") !== selectedType;
      });
    });

    document.querySelectorAll("[data-backup-delete]").forEach((button) => {
      button.addEventListener("click", async () => {
        const backupId = button.getAttribute("data-backup-delete");
        if (!backupId) {
          return;
        }
        try {
          setActionBusy(button, true);
          await requestJson(`/api/admin/backups/${backupId}`, { method: "DELETE" });
          showToastAfterReload("success", "Бэкап удалён");
          window.setTimeout(() => window.location.reload(), 200);
        } catch (error) {
          setActionBusy(button, false);
          showToast("error", error.message, "error");
        }
      });
    });

    const backupRestorePlanResult = document.querySelector("[data-backup-restore-plan-result]");
    const backupTypeLabel = (type) => {
      if (type === "users") return "пользовательский";
      if (type === "system") return "системный";
      if (type === "full") return "полный";
      return String(type || "unknown");
    };

    const renderBackupPreviewCards = (plan) => {
      const cards = document.createElement("div");
      cards.className = "backup-preview-grid";
      const summary = plan.summary || {};
      const system = plan.system_summary || {};
      [
        ["Пользовательские данные", `Пользователи: ${summary.backup_users_total || 0}. Интерфейсы: ${summary.interfaces || 0}. Пиры: ${summary.peers || 0}. Ресурсы: ${summary.resources || 0}. Фильтры: ${summary.filters || 0}.`],
        ["Системные данные", `Настройки: ${system.settings || 0}. Серверы: ${system.servers || 0}. Критичные логи: ${system.critical_logs || 0}.`],
        ["Файлы архива", `Всего файлов: ${summary.archive_files || 0}. Конфиги пиров: ${summary.peer_config_files || 0}. Snapshot серверов: ${summary.server_snapshots || 0}.`],
        ["Режим восстановления", `Пользователи: ${plan.can_restore_users ? "доступно" : "preview"}. Система: ${plan.can_restore_system ? "доступно" : "preview"}. Серверы: ${plan.can_restore_server_snapshots ? "доступно" : "preview"}.`],
      ].forEach(([title, text]) => {
        const card = document.createElement("div");
        card.className = "backup-preview-card";
        card.innerHTML = "<strong></strong><p></p>";
        card.querySelector("strong").textContent = title;
        card.querySelector("p").textContent = text;
        cards.appendChild(card);
      });
      return cards;
    };

    const renderServerSnapshotPreview = (plan) => {
      if (!(plan.server_snapshots || []).length) {
        return null;
      }
      const wrap = document.createElement("div");
      wrap.className = "backup-plan-users";
      const title = document.createElement("p");
      title.className = "muted-note";
      title.textContent = "Серверные snapshot-файлы в backup:";
      wrap.appendChild(title);
      (plan.server_snapshots || []).forEach((snapshot) => {
        const row = document.createElement("div");
        row.className = "backup-plan-user";
        row.innerHTML = `
          <div>
            <strong></strong>
            <p></p>
          </div>
          <span class="availability"></span>
        `;
        row.querySelector("strong").textContent = `${snapshot.name || "server"} · ${String(snapshot.server_type || "").toUpperCase()}`;
        row.querySelector("p").textContent = `${snapshot.filename || "без файла"} · ${snapshot.size_bytes || 0} bytes`;
        const badge = row.querySelector(".availability");
        badge.textContent = snapshot.status || "unknown";
        badge.classList.add(snapshot.status === "included" ? "is-online" : "is-invalid");
        wrap.appendChild(row);
      });
      return wrap;
    };

    const renderBackupRestorePlan = (plan) => {
      if (!backupRestorePlanResult) {
        return;
      }
      backupRestorePlanResult.hidden = false;
      backupRestorePlanResult.innerHTML = "";
      const header = document.createElement("div");
      header.className = "backup-plan-header";
      header.innerHTML = `
        <div>
          <strong></strong>
          <p></p>
        </div>
        <span class="availability"></span>
      `;
      header.querySelector("strong").textContent = `План восстановления: ${plan.filename}`;
      header.querySelector("p").textContent = `Версия: ${plan.backup_version}. Пользователи: ${plan.summary?.users || 0}. Интерфейсы: ${plan.summary?.interfaces || 0}. Пиры: ${plan.summary?.peers || 0}. Конфликты: ${plan.summary?.conflicts || 0}.`;
      const badge = header.querySelector(".availability");
      badge.textContent = plan.can_restore_users ? "dry-run" : "нет user data";
      badge.classList.add(plan.can_restore_users ? "is-online" : "is-invalid");
      header.querySelector("strong").textContent = `Проверка восстановления: ${plan.filename}`;
      header.querySelector("p").textContent = `Тип: ${backupTypeLabel(plan.backup_type)}. Версия: ${plan.backup_version}. Конфликты: ${plan.summary?.conflicts || 0}.`;
      header.querySelector("p").textContent = `Тип: ${backupTypeLabel(plan.backup_type)}. Версия: ${plan.backup_version}. Режим: ${plan.restore_scope || "preview_only"}. Конфликты: ${plan.summary?.conflicts || 0}.`;
      badge.textContent = plan.can_restore_users ? "users restore ready" : "preview only";
      backupRestorePlanResult.appendChild(header);
      backupRestorePlanResult.appendChild(renderBackupPreviewCards(plan));

      if (plan.can_restore_users && (plan.users || []).length) {
        const draftToolbar = document.createElement("div");
        draftToolbar.className = "toolbar-row";
        draftToolbar.innerHTML = `
          <button class="ghost-button small" type="button" data-backup-select-all>Выбрать всех</button>
          <button class="ghost-button small" type="button" data-backup-select-none>Снять выбор</button>
          <button class="primary-button small" type="button" data-backup-draft-plan>Пересчитать выбранных</button>
          <button class="ghost-button small danger" type="button" data-backup-restore-users>Восстановить выбранных</button>
        `;
        draftToolbar.querySelector("[data-backup-select-all]").textContent = "Выбрать всех";
        draftToolbar.querySelector("[data-backup-select-none]").textContent = "Снять выбор";
        draftToolbar.querySelector("[data-backup-draft-plan]").textContent = "Пересчитать выбранных";
        draftToolbar.querySelector("[data-backup-restore-users]").textContent = "Восстановить выбранных";
        backupRestorePlanResult.appendChild(draftToolbar);
      }

      (plan.warnings || []).forEach((warning) => {
        const node = document.createElement("p");
        node.className = "status-text is-error";
        node.textContent = warning;
        backupRestorePlanResult.appendChild(node);
      });

      const list = document.createElement("div");
      list.className = "backup-plan-users";
      (plan.users || []).forEach((user) => {
        const row = document.createElement("div");
        row.className = "backup-plan-user";
        const conflicts = (user.conflicts || []).map((item) => `${item.severity}: ${item.message}`).join(" | ");
        const hasBlockingConflicts = (user.conflicts || []).some((item) => item.severity === "choice_required");
        row.innerHTML = `
          <div>
            <label class="backup-user-choice">
              <input type="checkbox">
              <strong></strong>
            </label>
            <p></p>
            <div class="backup-conflict-actions" hidden></div>
          </div>
          <span class="availability"></span>
        `;
        const checkbox = row.querySelector('input[type="checkbox"]');
        checkbox.setAttribute("data-backup-user-choice", String(user.backup_user_id));
        checkbox.checked = user.selected !== false;
        row.querySelector("strong").textContent = `${user.login} · ${user.display_name || "-"}`;
        row.querySelector("p").textContent = `Интерфейсы: ${user.interface_count}. Пиры: ${user.peer_count}.${conflicts ? ` ${conflicts}` : ""}`;
        row.querySelector("strong").textContent = `${user.login} · ${user.display_name || "-"}`;
        row.querySelector("p").textContent = `Интерфейсы: ${user.interface_count}. Пиры: ${user.peer_count}.${conflicts ? ` ${conflicts}` : ""}`;
        const conflictActions = row.querySelector(".backup-conflict-actions");
        if (hasBlockingConflicts && conflictActions) {
          conflictActions.hidden = false;
          const interfaceIds = Array.from(new Set((user.conflicts || [])
            .map((item) => Number(item.backup_interface_id))
            .filter((value) => Number.isFinite(value))));
          conflictActions.innerHTML = `
            <label>
              Новый логин
              <input type="text" data-backup-login-override="${user.backup_user_id}" placeholder="${user.login}-restored">
            </label>
            ${interfaceIds.map((interfaceId) => `
              <label>
                Новый порт interface ${interfaceId}
                <input type="number" min="1" max="65535" data-backup-port-override="${interfaceId}">
              </label>
              <label>
                Новый IPv4 interface ${interfaceId}
                <input type="text" data-backup-address-override="${interfaceId}" placeholder="10.0.0.1">
              </label>
            `).join("")}
          `;
          conflictActions.querySelectorAll("label").forEach((label, index) => {
            const input = label.querySelector("input");
            const interfaceId = input?.getAttribute("data-backup-port-override") || input?.getAttribute("data-backup-address-override");
            const prefix = index === 0 ? "Новый логин" : input?.hasAttribute("data-backup-port-override") ? `Новый порт interface ${interfaceId}` : `Новый IPv4 interface ${interfaceId}`;
            label.childNodes[0].textContent = `${prefix} `;
          });
        }
        const userBadge = row.querySelector(".availability");
        userBadge.textContent = user.status;
        userBadge.classList.add(user.status === "conflict" || user.status === "login_conflict" ? "is-invalid" : "is-online");
        list.appendChild(row);
      });
      if (!(plan.users || []).length) {
        const empty = document.createElement("p");
        empty.className = "muted-note";
        empty.textContent = "В этом бэкапе нет пользователей для восстановления.";
        empty.textContent = "В этом бэкапе нет выбранных пользователей для восстановления.";
        list.appendChild(empty);
      }
      backupRestorePlanResult.appendChild(list);
      const snapshots = renderServerSnapshotPreview(plan);
      if (snapshots) {
        backupRestorePlanResult.appendChild(snapshots);
      }
    };

    backupRestorePlanResult?.addEventListener("click", async (event) => {
      const selectAll = event.target.closest("[data-backup-select-all]");
      const selectNone = event.target.closest("[data-backup-select-none]");
      const draftButton = event.target.closest("[data-backup-draft-plan]");
      const restoreButton = event.target.closest("[data-backup-restore-users]");
      if (selectAll || selectNone) {
        backupRestorePlanResult.querySelectorAll("[data-backup-user-choice]").forEach((checkbox) => {
          checkbox.checked = Boolean(selectAll);
        });
        return;
      }
      if (!draftButton && !restoreButton) {
        return;
      }
      const backupId = backupRestorePlanResult.getAttribute("data-current-backup-id");
      if (!backupId) {
        return;
      }
      const userIds = Array.from(backupRestorePlanResult.querySelectorAll("[data-backup-user-choice]"))
        .filter((checkbox) => checkbox.checked)
        .map((checkbox) => Number(checkbox.getAttribute("data-backup-user-choice")))
        .filter((value) => Number.isFinite(value));
      const loginOverrides = {};
      backupRestorePlanResult.querySelectorAll("[data-backup-login-override]").forEach((input) => {
        const userId = Number(input.getAttribute("data-backup-login-override"));
        const value = input.value.trim();
        if (Number.isFinite(userId) && value) {
          loginOverrides[userId] = value;
        }
      });
      const portOverrides = {};
      backupRestorePlanResult.querySelectorAll("[data-backup-port-override]").forEach((input) => {
        const interfaceId = Number(input.getAttribute("data-backup-port-override"));
        const value = Number(input.value);
        if (Number.isFinite(interfaceId) && Number.isFinite(value) && value > 0) {
          portOverrides[interfaceId] = value;
        }
      });
      const addressOverrides = {};
      backupRestorePlanResult.querySelectorAll("[data-backup-address-override]").forEach((input) => {
        const interfaceId = Number(input.getAttribute("data-backup-address-override"));
        const value = input.value.trim();
        if (Number.isFinite(interfaceId) && value) {
          addressOverrides[interfaceId] = value;
        }
      });
      if (restoreButton && !window.confirm("Восстановить выбранных пользователей из бэкапа? Операция создаст новые записи в панели.")) {
        return;
      }
      try {
        const actionButton = draftButton || restoreButton;
        setActionBusy(actionButton, true);
        const endpoint = restoreButton ? "restore-users" : "restore-plan";
        const result = await requestJson(`/api/admin/backups/${backupId}/${endpoint}`, {
          method: "POST",
          body: JSON.stringify({
            user_ids: userIds,
            user_login_overrides: loginOverrides,
            interface_port_overrides: portOverrides,
            interface_address_overrides: addressOverrides,
          }),
        });
        const plan = restoreButton ? result.plan : result;
        renderBackupRestorePlan(plan);
        backupRestorePlanResult.setAttribute("data-current-backup-id", backupId);
        showToast(
          "success",
          restoreButton
            ? `Восстановлено: пользователи ${result.restored_users}, интерфейсы ${result.restored_interfaces}, пиры ${result.restored_peers}`
            : "Draft-план пересчитан"
        );
      } catch (error) {
        showToast("error", error.message, "error");
      } finally {
        setActionBusy(draftButton || restoreButton, false);
      }
    });

    document.querySelectorAll("[data-backup-restore-plan]").forEach((button) => {
      button.addEventListener("click", async () => {
        const backupId = button.getAttribute("data-backup-restore-plan");
        if (!backupId) {
          return;
        }
        try {
          setActionBusy(button, true);
          const plan = await requestJson(`/api/admin/backups/${backupId}/restore-plan`);
          renderBackupRestorePlan(plan);
          backupRestorePlanResult?.setAttribute("data-current-backup-id", backupId);
          showToast("success", "План восстановления построен");
        } catch (error) {
          showToast("error", error.message, "error");
        } finally {
          setActionBusy(button, false);
        }
      });
    });

    const auditLogSettingsForm = document.querySelector("[data-audit-log-settings-form]");
    auditLogSettingsForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const statusNode = auditLogSettingsForm.querySelector("[data-audit-log-settings-status]");
      const submitButton = auditLogSettingsForm.querySelector('button[type="submit"]');
      const formData = new FormData(auditLogSettingsForm);
      try {
        setActionBusy(submitButton, true);
        setStatus(statusNode, "Сохраняем...");
        await requestJson("/api/admin/settings/logs", {
          method: "PUT",
          body: JSON.stringify({ retention_days: Number(formData.get("retention_days") || 30) }),
        });
        setStatus(statusNode, "Сохранено");
        showToast("success", "Срок хранения логов сохранён");
      } catch (error) {
        setStatus(statusNode, error.message, true);
        showToast("error", error.message, "error");
      } finally {
        setActionBusy(submitButton, false);
      }
    });

    const auditLogCleanupForm = document.querySelector("[data-audit-log-cleanup-form]");
    auditLogCleanupForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const statusNode = auditLogCleanupForm.querySelector("[data-audit-log-cleanup-status]");
      const submitButton = auditLogCleanupForm.querySelector('button[type="submit"]');
      const formData = new FormData(auditLogCleanupForm);
      if (!window.confirm("Удалить старые логи?")) {
        return;
      }
      try {
        setActionBusy(submitButton, true);
        setStatus(statusNode, "Удаляем...");
        const result = await requestJson("/api/admin/logs/cleanup", {
          method: "POST",
          body: JSON.stringify({ keep_days: Number(formData.get("keep_days") || 30) }),
        });
        setStatus(statusNode, `Удалено: ${result.deleted || 0}`);
        showToast("success", `Удалено логов: ${result.deleted || 0}`);
      } catch (error) {
        setStatus(statusNode, error.message, true);
        showToast("error", error.message, "error");
      } finally {
        setActionBusy(submitButton, false);
      }
    });

    document.querySelector("[data-audit-log-delete-all-form]")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const submitButton = form.querySelector('button[type="submit"]');
      const statusNode = document.querySelector("[data-audit-log-delete-all-status]");
      if (!window.confirm("Удалить все логи?")) {
        return;
      }
      try {
        setActionBusy(submitButton, true);
        setStatus(statusNode, "Удаляем...");
        await requestJson("/api/admin/logs", { method: "DELETE" });
        setStatus(statusNode, "Все логи удалены");
        showToast("success", "Все логи удалены");
      } catch (error) {
        setStatus(statusNode, error.message, true);
        showToast("error", error.message, "error");
      } finally {
        setActionBusy(submitButton, false);
      }
    });

    const globalFilterForm = document.querySelector("[data-admin-global-filter-form]");
    globalFilterForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const statusNode = globalFilterForm.querySelector("[data-filter-status]");
      const formData = new FormData(globalFilterForm);
      const payload = {
        ...Object.fromEntries(formData.entries()),
        scope: "global",
        kind: globalFilterForm.getAttribute("data-filter-kind") || "exclusion",
        is_active: true,
      };
      try {
        setStatus(statusNode, "Добавляем...");
        validateFilterPayload(payload);
        await requestJson("/api/admin/filters", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        setStatus(statusNode, "Добавлено");
        showToastAfterReload("success", "Глобальный фильтр добавлен");
        window.setTimeout(() => window.location.reload(), 400);
      } catch (error) {
        setStatus(statusNode, error.message, true);
        showToast("error", error.message, "error");
      }
    });

    document.querySelectorAll("[data-interface-toggle]").forEach((checkbox) => {
      checkbox.addEventListener("change", async () => {
        const interfaceId = checkbox.getAttribute("data-interface-toggle");
        if (!interfaceId) {
          return;
        }
        try {
          await requestJson(`/api/admin/interfaces/${interfaceId}/toggle`, { method: "POST" });
          showToastAfterReload("success", "Статус интерфейса обновлён");
          window.setTimeout(() => window.location.reload(), 150);
        } catch (error) {
          checkbox.checked = !checkbox.checked;
          window.alert(error.message);
          showToast("error", error.message, "error");
        }
      });
    });

    const clientCreateForm = document.querySelector("[data-client-create-form]");
    clientCreateForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const statusNode = clientCreateForm.querySelector("[data-client-status]");
      const formData = new FormData(clientCreateForm);
      const interfaceIds = Array.from(
        clientCreateForm.querySelector('select[name="interface_ids"]').selectedOptions,
      ).map((option) => Number(option.value));
      const payload = {
        login: String(formData.get("login") || ""),
        password: String(formData.get("password") || ""),
        interface_ids: interfaceIds,
        display_name: String(formData.get("display_name") || ""),
        communication_channel: String(formData.get("communication_channel") || ""),
      };
      try {
        setStatus(statusNode, "Создаём...");
        await requestJson("/api/admin/users", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        setStatus(statusNode, "Пользователь создан");
        showToastAfterReload("success", "Пользователь создан");
        window.setTimeout(() => window.location.reload(), 400);
      } catch (error) {
        setStatus(statusNode, error.message, true);
        showToast("error", error.message, "error");
      }
    });

    document.querySelectorAll("[data-client-delete]").forEach((button) => {
      button.addEventListener("click", async () => {
        const userId = button.getAttribute("data-client-delete");
        if (!userId) {
          return;
        }
        try {
          await requestJson(`/api/admin/users/${userId}`, { method: "DELETE" });
          showToastAfterReload("success", "Пользователь удалён");
          window.setTimeout(() => window.location.reload(), 200);
        } catch (error) {
          window.alert(error.message);
          showToast("error", error.message, "error");
        }
      });
    });

    document.querySelectorAll("[data-interface-delete]").forEach((button) => {
      button.addEventListener("click", async () => {
        const interfaceId = button.getAttribute("data-interface-delete");
        if (!interfaceId) {
          return;
        }
        try {
          await requestJson(`/api/admin/interfaces/${interfaceId}`, { method: "DELETE" });
          showToastAfterReload("success", "Интерфейс удалён");
          window.setTimeout(() => window.location.reload(), 200);
        } catch (error) {
          window.alert(error.message);
          showToast("error", error.message, "error");
        }
      });
    });

    document.querySelectorAll("[data-client-channel-save]").forEach((button) => {
      button.addEventListener("click", async () => {
        const userId = button.getAttribute("data-client-channel-save");
        const input = document.querySelector(`[data-client-channel-input="${userId}"]`);
        if (!userId || !input) {
          return;
        }
        try {
          await requestJson(`/api/admin/users/${userId}/channel`, {
            method: "PUT",
            body: JSON.stringify({ value: input.value }),
          });
          showToastAfterReload("success", "Канал связи сохранён");
          window.setTimeout(() => window.location.reload(), 200);
        } catch (error) {
          window.alert(error.message);
          showToast("error", error.message, "error");
        }
      });
    });

    document.querySelectorAll("[data-client-name-save]").forEach((button) => {
      button.addEventListener("click", async () => {
        const userId = button.getAttribute("data-client-name-save");
        const input = document.querySelector(`[data-client-name-input="${userId}"]`);
        if (!userId || !input) {
          return;
        }
        try {
          await requestJson(`/api/admin/users/${userId}/name`, {
            method: "PUT",
            body: JSON.stringify({ value: input.value }),
          });
          showToastAfterReload("success", "Имя пользователя сохранено");
          window.setTimeout(() => window.location.reload(), 200);
        } catch (error) {
          window.alert(error.message);
          showToast("error", error.message, "error");
        }
      });
    });

    const clientSearchInput = document.querySelector("[data-client-search]");
    clientSearchInput?.addEventListener("input", () => {
      const needle = String(clientSearchInput.value || "").trim().toLocaleLowerCase("ru-RU");
      document.querySelectorAll("[data-client-card]").forEach((card) => {
        const haystack = String(card.getAttribute("data-client-card-name") || "").toLocaleLowerCase("ru-RU");
        card.hidden = needle.length > 0 && !haystack.includes(needle);
      });
    });

    document.querySelectorAll("[data-admin-filter-delete]").forEach((button) => {
      button.addEventListener("click", async () => {
        const ids = JSON.parse(button.getAttribute("data-admin-filter-delete") || "[]");
        if (!ids.length) {
          return;
        }
        try {
          await requestJson("/api/admin/filters/delete", {
            method: "POST",
            body: JSON.stringify({ ids }),
          });
          showToastAfterReload("success", "Фильтр удалён");
          window.setTimeout(() => window.location.reload(), 200);
        } catch (error) {
          window.alert(error.message);
          showToast("error", error.message, "error");
        }
      });
    });

    interfaceCreateForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const statusNode = interfaceCreateForm.querySelector("[data-interface-status]");
      const formData = new FormData(interfaceCreateForm);
      const payload = {
        name: String(formData.get("name") || ""),
        tic_server_id: Number(formData.get("tic_server_id") || 0),
        tak_server_id: formData.get("tak_server_id") ? Number(formData.get("tak_server_id")) : null,
        listen_port: formData.get("listen_port") ? Number(formData.get("listen_port")) : null,
        address_v4: String(formData.get("address_v4") || ""),
        peer_limit: Number(formData.get("peer_limit") || 5),
      };
      if (!payload.address_v4) {
        payload.address_v4 = null;
      }
      try {
        setStatus(statusNode, "Создаём...");
        await requestJson("/api/admin/interfaces", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        setStatus(statusNode, "Интерфейс создан");
        showToastAfterReload("success", "Интерфейс создан");
        window.setTimeout(() => {
          if (interfaceModal) {
            interfaceModal.hidden = true;
          }
          window.location.reload();
        }, 300);
      } catch (error) {
        setStatus(statusNode, error.message, true);
        showToast("error", error.message, "error");
      }
    });
  }

  if (adminJobsPage) {
    const jobsStatusNode = document.querySelector("[data-panel-jobs-status]");
    const shouldAutoRefreshJobs = adminJobsPage.getAttribute("data-auto-refresh") === "1";
    if (shouldAutoRefreshJobs) {
      window.setTimeout(() => {
        window.location.reload();
      }, 12000);
      setStatus(jobsStatusNode, "Страница обновляется автоматически, пока есть активные задачи.");
    }

    document.querySelector("[data-run-expired-peers-job]")?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      try {
        setActionBusy(button, true);
        setStatus(jobsStatusNode, "Запускаем задачу...");
        await requestJson("/api/admin/jobs/expired-peers/run", { method: "POST" });
        showToastAfterReload("success", "Очистка истёкших пиров завершена");
        window.setTimeout(() => window.location.reload(), 200);
      } catch (error) {
        setStatus(jobsStatusNode, error.message, true);
        showToast("error", error.message, "error");
      } finally {
        setActionBusy(button, false);
      }
    });

    document.querySelectorAll("[data-panel-job-cancel]").forEach((button) => {
      button.addEventListener("click", async () => {
        const jobId = button.getAttribute("data-panel-job-cancel");
        if (!jobId) {
          return;
        }
        try {
          setActionBusy(button, true);
          await requestJson(`/api/admin/jobs/${jobId}/cancel`, { method: "POST" });
          showToastAfterReload("success", "Задача остановлена");
          window.setTimeout(() => window.location.reload(), 200);
        } catch (error) {
          setStatus(jobsStatusNode, error.message, true);
          showToast("error", error.message, "error");
        } finally {
          setActionBusy(button, false);
        }
      });
    });
  }

  if (adminServersPage) {
    const serverModal = document.querySelector("[data-server-modal]");
    const serverCreateForm = document.querySelector("[data-server-create-form]");
    const serverTypeSelect = serverCreateForm?.querySelector("[data-server-type-select]") || null;
    const serverTicRegionField = serverCreateForm?.querySelector("[data-server-tic-region-field]") || null;
    const serverTakCountryField = serverCreateForm?.querySelector("[data-server-tak-country-field]") || null;
    const serverTicRegionInput = serverCreateForm?.querySelector('select[name="tic_region"]') || null;
    const serverTakCountryInput = serverCreateForm?.querySelector('input[name="tak_country"]') || null;
    const bootstrapConsole = document.querySelector("[data-bootstrap-console]");
    const bootstrapInputWrap = null;
    const bootstrapInputPrompt = null;
    const bootstrapInput = null;
    const bootstrapInputSubmit = null;
    const serverRefreshButton = document.querySelector("[data-server-refresh]");
    const runtimeResultNode = document.querySelector("[data-server-runtime-result]");
    const runtimeSummaryNode = document.querySelector("[data-runtime-summary]");
    const runtimeBadgeNode = document.querySelector("[data-runtime-badge]");
    const runtimePathsNode = document.querySelector("[data-runtime-paths]");
    const runtimeModeNode = document.querySelector("[data-runtime-mode]");
    const runtimeRootNode = document.querySelector("[data-runtime-root]");
    const runtimeWgRootNode = document.querySelector("[data-runtime-wg-root]");
    const runtimePeersRootNode = document.querySelector("[data-runtime-peers-root]");
    const runtimeChecksNode = document.querySelector("[data-runtime-checks]");
    let currentBootstrapTaskId = null;
    let bootstrapPollTimer = null;
    let serverAutoRefreshTimer = null;

    const syncServerLocationFields = () => {
      const serverType = String(serverTypeSelect?.value || "tic");
      if (serverTicRegionField) {
        serverTicRegionField.hidden = serverType !== "tic";
      }
      if (serverTakCountryField) {
        serverTakCountryField.hidden = serverType !== "tak";
      }
      if (serverTicRegionInput) {
        serverTicRegionInput.required = serverType === "tic";
        if (serverType !== "tic") {
          serverTicRegionInput.value = "";
        }
      }
      if (serverTakCountryInput) {
        serverTakCountryInput.required = serverType === "tak";
        if (serverType !== "tak") {
          serverTakCountryInput.value = "";
        }
      }
    };

    const ensureBootstrapInputWrap = (container) => {
      if (!container) {
        return null;
      }
      let wrap = container.querySelector("[data-bootstrap-input-wrap]");
      if (wrap) {
        return wrap;
      }
      wrap = document.createElement("div");
      wrap.className = "bootstrap-input-wrap";
      wrap.hidden = true;
      wrap.setAttribute("data-bootstrap-input-wrap", "");
      wrap.innerHTML = `
        <p class="bootstrap-input-prompt" data-bootstrap-input-prompt></p>
        <p class="bootstrap-input-hint" data-bootstrap-input-hint hidden></p>
        <div class="bootstrap-input-row">
          <input type="text" data-bootstrap-input autocomplete="off">
          <button class="primary-button small" type="button" data-bootstrap-input-submit>Продолжить</button>
        </div>
      `;
      const toolbarRow = container.querySelector(".toolbar-row");
      if (toolbarRow) {
        container.insertBefore(wrap, toolbarRow);
      } else {
        container.appendChild(wrap);
      }
      return wrap;
    };

    const detailBootstrapInputWrap = adminServersPage.querySelector(".server-detail-card [data-bootstrap-input-wrap]");
    const modalBootstrapInputWrap = ensureBootstrapInputWrap(serverCreateForm);

    const resolveActiveBootstrapInputWrap = () => {
      if (serverModal && !serverModal.hidden && modalBootstrapInputWrap) {
        return modalBootstrapInputWrap;
      }
      return detailBootstrapInputWrap || modalBootstrapInputWrap;
    };

    const resolveActiveBootstrapControls = () => {
      const wrap = resolveActiveBootstrapInputWrap();
      return {
        wrap,
        prompt: wrap?.querySelector("[data-bootstrap-input-prompt]") || null,
        hint: wrap?.querySelector("[data-bootstrap-input-hint]") || null,
        input: wrap?.querySelector("[data-bootstrap-input]") || null,
        submit: wrap?.querySelector("[data-bootstrap-input-submit]") || null,
      };
    };

    const writeBootstrapLog = (lines) => {
      if (!bootstrapConsole) {
        return;
      }
      bootstrapConsole.textContent = Array.isArray(lines) ? lines.join("\n") : String(lines || "");
      bootstrapConsole.scrollTop = bootstrapConsole.scrollHeight;
    };

    const stopBootstrapPolling = () => {
      if (bootstrapPollTimer) {
        window.clearTimeout(bootstrapPollTimer);
        bootstrapPollTimer = null;
      }
    };

    const scheduleServerAutoRefresh = () => {
      if (serverAutoRefreshTimer) {
        window.clearTimeout(serverAutoRefreshTimer);
      }
      serverAutoRefreshTimer = window.setTimeout(() => {
        const modalIsOpen = !!serverModal && !serverModal.hidden;
        const bootstrapNeedsInput = !resolveActiveBootstrapControls().wrap?.hidden;
        if (modalIsOpen || currentBootstrapTaskId || bootstrapNeedsInput) {
          scheduleServerAutoRefresh();
          return;
        }
        window.location.reload();
      }, 60000);
    };

    const setBootstrapInputState = (task) => {
      if (!bootstrapInputWrap || !bootstrapInputPrompt || !bootstrapInput || !bootstrapInputSubmit) {
        return;
      }
      const needsInput = task?.status === "input_required";
      bootstrapInputWrap.hidden = !needsInput;
      if (!needsInput) {
        bootstrapInput.value = "";
        bootstrapInput.hidden = false;
        bootstrapInput.removeAttribute("data-input-kind");
        return;
      }
      bootstrapInputPrompt.textContent = String(task.input_prompt || "Требуется дополнительный ввод.");
      const inputKind = String(task.input_kind || "text");
      bootstrapInput.setAttribute("data-input-kind", inputKind);
      if (inputKind === "confirm") {
        bootstrapInput.hidden = true;
        bootstrapInput.value = "yes";
        bootstrapInputSubmit.textContent = "Подтвердить";
      } else {
        bootstrapInput.hidden = false;
        bootstrapInput.value = "";
        bootstrapInputSubmit.textContent = "Продолжить";
        bootstrapInput.focus();
      }
    };

    const describeBootstrapPrompt = (task) => {
      const inputKey = String(task?.input_key || "");
      const inputKind = String(task?.input_kind || "text");
      if (inputKey === "ssh_host_key_confirm") {
        return {
          prompt: "Подтвердить SSH host key удалённого сервера",
          hint: "Панель ждёт подтверждение первого SSH-подключения к новому хосту.",
          buttonText: "Подтвердить host key",
          placeholder: "",
          hideInput: true,
          waitLabel: "Ожидание: ssh_host_key_confirm",
        };
      }
      if (inputKey === "ssh_password" || inputKind === "password") {
        return {
          prompt: "Введите SSH пароль удалённого сервера",
          hint: "Пароль будет отправлен только в bootstrap-задачу и не отображается в поле.",
          buttonText: "Отправить пароль",
          placeholder: "Введите пароль",
          hideInput: false,
          waitLabel: "Ожидание: ssh_password",
        };
      }
      if (/^bootstrap_step_\d+_confirm$/.test(inputKey)) {
        const stepNumber = inputKey.match(/\d+/)?.[0] || "?";
        return {
          prompt: `Подтвердить выполнение bootstrap шага ${stepNumber}`,
          hint: String(task?.input_prompt || "Агент запросил подтверждение следующей команды bootstrap."),
          buttonText: "Подтвердить шаг",
          placeholder: "",
          hideInput: true,
          waitLabel: `Ожидание: шаг ${stepNumber} требует подтверждения (${inputKey})`,
        };
      }
      return {
        prompt: String(task?.input_prompt || "Требуется дополнительный ввод."),
        hint: "",
        buttonText: inputKind === "confirm" ? "Подтвердить" : "Продолжить",
        placeholder: inputKind === "password" ? "Введите пароль" : "Введите значение",
        hideInput: inputKind === "confirm",
        waitLabel: inputKey ? `Ожидание: ${inputKey}` : "Ожидание дополнительного ввода",
      };
    };

    const resolveBootstrapPendingCommand = (task) => {
      if (!task || task.status !== "input_required" || !Array.isArray(task.logs)) {
        return "";
      }
      const inputKey = String(task.input_key || "");
      if (!/^bootstrap_step_\d+_confirm$/.test(inputKey)) {
        return "";
      }
      const waitLine = [...task.logs].reverse().find((line) => /^WAIT step \d+: /.test(String(line || "")));
      if (!waitLine) {
        return "";
      }
      const match = String(waitLine).match(/^WAIT step \d+: (.+)$/);
      return match ? String(match[1] || "").trim() : "";
    };

    const bootstrapSnapshotLines = (task) => {
      const snapshot = task?.bootstrap_snapshot;
      if (!snapshot || typeof snapshot !== "object") {
        return [];
      }
      const lines = [];
      if (snapshot.transport) {
        lines.push(`Transport: ${snapshot.transport}`);
      }
      if (snapshot.mode) {
        lines.push(`Режим bootstrap: ${snapshot.mode}`);
      }
      if (typeof snapshot.executed_step_count === "number" && typeof snapshot.command_count === "number") {
        lines.push(`Шаги: ${snapshot.executed_step_count}/${snapshot.command_count}`);
      }
      if (snapshot.current_step_index) {
        lines.push(`Текущий шаг: ${snapshot.current_step_index}${snapshot.current_step_status ? ` (${snapshot.current_step_status})` : ""}`);
      }
      if (snapshot.resume_from_step) {
        lines.push(`Точка возобновления: шаг ${snapshot.resume_from_step}`);
      }
      lines.push(snapshot.applied ? "Bootstrap выполняется в apply-режиме" : "Bootstrap пока в planned/dry-run состоянии");
      return lines;
    };

    const bootstrapStatusSummary = (task) => {
      const snapshot = task?.bootstrap_snapshot;
      if (!snapshot || typeof snapshot !== "object") {
        return "";
      }
      const parts = [];
      if (snapshot.transport) {
        parts.push(`transport: ${snapshot.transport}`);
      }
      if (snapshot.current_step_index) {
        parts.push(`шаг ${snapshot.current_step_index}`);
      }
      if (typeof snapshot.executed_step_count === "number" && typeof snapshot.command_count === "number" && snapshot.command_count > 0) {
        parts.push(`${snapshot.executed_step_count}/${snapshot.command_count}`);
      }
      if (snapshot.waiting_for_input) {
        parts.push("ожидает ввод");
      }
      return parts.join(" · ");
    };

    const setTypedBootstrapInputState = (task) => {
      const { wrap, prompt, hint, input, submit } = resolveActiveBootstrapControls();
      if (!wrap || !prompt || !input || !submit) {
        return;
      }
      const needsInput = task?.status === "input_required";
      wrap.hidden = !needsInput;
      if (!needsInput) {
        input.value = "";
        input.hidden = false;
        input.type = "text";
        input.placeholder = "";
        input.removeAttribute("data-input-key");
        input.removeAttribute("data-input-kind");
        if (hint) {
          hint.hidden = true;
          hint.textContent = "";
        }
        return;
      }
      const descriptor = describeBootstrapPrompt(task);
      prompt.textContent = descriptor.prompt;
      const inputKind = String(task.input_kind || "text");
      input.setAttribute("data-input-key", String(task.input_key || ""));
      input.setAttribute("data-input-kind", inputKind);
      if (inputKind === "confirm") {
        input.hidden = descriptor.hideInput;
        input.type = "text";
        input.value = "yes";
        input.placeholder = descriptor.placeholder;
        submit.textContent = descriptor.buttonText;
        if (hint) {
          hint.hidden = !descriptor.hint;
          hint.textContent = descriptor.hint || "Будет отправлено подтверждение yes.";
        }
      } else if (inputKind === "password") {
        input.hidden = false;
        input.type = "password";
        input.value = "";
        input.placeholder = descriptor.placeholder;
        submit.textContent = descriptor.buttonText;
        if (hint) {
          hint.hidden = !descriptor.hint;
          hint.textContent = descriptor.hint || "Пароль отображаться не будет.";
        }
      } else {
        input.hidden = false;
        input.type = "text";
        input.value = "";
        input.placeholder = descriptor.placeholder;
        submit.textContent = descriptor.buttonText;
        if (hint) {
          hint.hidden = !descriptor.hint;
          hint.textContent = descriptor.hint;
        }
        input.focus();
      }
    };

    const renderBootstrapTask = (task, statusNode) => {
      currentBootstrapTaskId = task?.id || currentBootstrapTaskId;
      const logLines = Array.isArray(task?.logs) ? [...task.logs] : [];
      logLines.push(...bootstrapSnapshotLines(task));
      if (task?.status === "input_required") {
        const descriptor = describeBootstrapPrompt(task);
        if (descriptor.waitLabel) {
          logLines.push(descriptor.waitLabel);
        }
        const pendingCommand = resolveBootstrapPendingCommand(task);
        if (pendingCommand) {
          logLines.push(`Команда шага: ${pendingCommand}`);
        }
      }
      writeBootstrapLog(logLines);
      setTypedBootstrapInputState(task);
      if (!task) {
        return;
      }
      if (task.status === "completed") {
        stopBootstrapPolling();
        setStatus(statusNode, "Сервер добавлен");
        if (task.server_id) {
          window.setTimeout(() => {
            window.location.href = `/admin/servers?selected_server_id=${task.server_id}`;
          }, 250);
        }
        return;
      }
      if (task.status === "failed") {
        stopBootstrapPolling();
        setStatus(statusNode, String(task.last_error || "Не удалось завершить настройку сервера."), true);
        return;
      }
      if (task.status === "cancelled") {
        stopBootstrapPolling();
        setStatus(statusNode, "Bootstrap остановлен администратором.", true);
        return;
      }
      const summary = bootstrapStatusSummary(task);
      if (task.status === "input_required" && summary) {
        stopBootstrapPolling();
        setStatus(statusNode, `Нужен ввод: ${summary}`);
        return;
      }
      if (task.status !== "completed" && task.status !== "failed" && task.status !== "cancelled" && summary) {
        setStatus(statusNode, `Настраиваем сервер: ${summary}`);
        return;
      }
      if (task.status === "input_required") {
        stopBootstrapPolling();
        setStatus(statusNode, "Нужен ввод для продолжения настройки...");
        return;
      }
      setStatus(statusNode, "Настраиваем сервер...");
    };

    const pollBootstrapTask = async (statusNode) => {
      if (!currentBootstrapTaskId) {
        return;
      }
      try {
        const task = await requestJson(`/api/admin/server-bootstrap/${currentBootstrapTaskId}`);
        renderBootstrapTask(task, statusNode);
        if (task.status === "running") {
          bootstrapPollTimer = window.setTimeout(() => pollBootstrapTask(statusNode), 1500);
        }
      } catch (error) {
        stopBootstrapPolling();
        setStatus(statusNode, error.message, true);
      }
    };

    const runServerAction = async (button, { endpoint, successMessage, redirectTo = null }) => {
      if (!button || !endpoint) {
        return;
      }
      try {
        setActionBusy(button, true);
        await requestJson(endpoint, { method: "POST" });
        showToastAfterReload("success", successMessage);
        window.setTimeout(() => {
          if (redirectTo) {
            window.location.href = redirectTo;
            return;
          }
          window.location.reload();
        }, 200);
      } catch (error) {
        setActionBusy(button, false);
        showToast("error", error.message, "error");
      }
    };

    const runtimeStatusClass = (status) => {
      const value = String(status || "").toLowerCase();
      if (value === "ok" || value === "ready" || value === "success") {
        return "is-online";
      }
      if (value === "warning") {
        return "is-confirmation";
      }
      if (value === "error" || value === "failed") {
        return "is-offline";
      }
      return "is-unknown";
    };

    const renderRuntimeCheck = (result) => {
      if (!runtimeResultNode || !runtimeSummaryNode || !runtimeBadgeNode || !runtimeChecksNode) {
        return;
      }
      runtimeResultNode.hidden = false;
      const ready = !!result?.ready;
      runtimeSummaryNode.textContent = ready
        ? "Среда агента готова к runtime-операциям"
        : "Среда агента не готова к runtime-операциям";
      runtimeBadgeNode.textContent = ready ? "ready" : "not ready";
      runtimeBadgeNode.className = `availability ${ready ? "is-online" : "is-offline"}`;
      if (runtimePathsNode) {
        runtimePathsNode.hidden = !(result?.mode || result?.runtime_root || result?.wireguard_root || result?.peers_root);
      }
      if (runtimeModeNode) {
        runtimeModeNode.textContent = result?.mode || "—";
      }
      if (runtimeRootNode) {
        runtimeRootNode.textContent = result?.runtime_root || "—";
      }
      if (runtimeWgRootNode) {
        runtimeWgRootNode.textContent = result?.wireguard_root || "—";
      }
      if (runtimePeersRootNode) {
        runtimePeersRootNode.textContent = result?.peers_root || "—";
      }
      runtimeChecksNode.textContent = "";
      const checks = Array.isArray(result?.checks) ? result.checks : [];
      checks.forEach((item) => {
        const card = document.createElement("article");
        card.className = "server-runtime-check";
        const body = document.createElement("div");
        const title = document.createElement("strong");
        title.textContent = String(item?.label || item?.key || "Check");
        const message = document.createElement("span");
        message.textContent = String(item?.message || "");
        body.appendChild(title);
        body.appendChild(message);
        const badge = document.createElement("span");
        badge.className = `availability ${runtimeStatusClass(item?.status)}`;
        badge.textContent = String(item?.status || "unknown");
        card.appendChild(body);
        card.appendChild(badge);
        runtimeChecksNode.appendChild(card);
      });
    };

    document.querySelectorAll("[data-server-restart-agent]").forEach((button) => {
      button.addEventListener("click", async (event) => {
        event.preventDefault();
        event.stopPropagation();
        const serverId = button.getAttribute("data-server-restart-agent");
        if (!serverId) {
          return;
        }
        await runServerAction(button, {
          endpoint: `/api/admin/servers/${serverId}/restart-agent`,
          successMessage: "Агент перезагружен",
        });
      });
    });

    document.querySelectorAll("[data-server-verify]").forEach((button) => {
      button.addEventListener("click", async (event) => {
        event.preventDefault();
        event.stopPropagation();
        const serverId = button.getAttribute("data-server-verify");
        if (!serverId) {
          return;
        }
        await runServerAction(button, {
          endpoint: `/api/admin/servers/${serverId}/refresh`,
          successMessage: "Статус сервера обновлён",
        });
      });
    });

    document.querySelectorAll("[data-server-runtime-check]").forEach((button) => {
      button.addEventListener("click", async (event) => {
        event.preventDefault();
        event.stopPropagation();
        const serverId = button.getAttribute("data-server-runtime-check");
        if (!serverId) {
          return;
        }
        try {
          setActionBusy(button, true);
          const result = await requestJson(`/api/admin/servers/${serverId}/runtime-check`, { method: "POST" });
          renderRuntimeCheck(result);
          showToast("success", result?.ready ? "Runtime среды готово" : "Runtime среды требует исправлений", result?.ready ? "success" : "error");
        } catch (error) {
          showToast("error", error.message, "error");
        } finally {
          setActionBusy(button, false);
        }
      });
    });

    document.querySelectorAll("[data-server-reboot]").forEach((button) => {
      button.addEventListener("click", async (event) => {
        event.preventDefault();
        event.stopPropagation();
        const serverId = button.getAttribute("data-server-reboot");
        if (!serverId) {
          return;
        }
        await runServerAction(button, {
          endpoint: `/api/admin/servers/${serverId}/reboot`,
          successMessage: "Команда перезагрузки сервера отправлена",
        });
      });
    });

    document.querySelectorAll("[data-server-exclude]").forEach((button) => {
      button.addEventListener("click", async (event) => {
        event.preventDefault();
        event.stopPropagation();
        const serverId = button.getAttribute("data-server-exclude");
        if (!serverId) {
          return;
        }
        await runServerAction(button, {
          endpoint: `/api/admin/servers/${serverId}/exclude`,
          successMessage: "Сервер исключён из окружения",
        });
      });
    });

    document.querySelectorAll("[data-server-restore]").forEach((button) => {
      button.addEventListener("click", async (event) => {
        event.preventDefault();
        event.stopPropagation();
        const serverId = button.getAttribute("data-server-restore");
        if (!serverId) {
          return;
        }
        await runServerAction(button, {
          endpoint: `/api/admin/servers/${serverId}/restore`,
          successMessage: "Сервер восстановлен",
        });
      });
    });

    document.querySelectorAll("[data-server-delete]").forEach((button) => {
      button.addEventListener("click", async (event) => {
        event.preventDefault();
        event.stopPropagation();
        const serverId = button.getAttribute("data-server-delete");
        if (!serverId) {
          return;
        }
        try {
          setActionBusy(button, true);
          await requestJson(`/api/admin/servers/${serverId}`, { method: "DELETE" });
          showToastAfterReload("success", "Сервер удалён из панели");
          window.setTimeout(() => { window.location.href = "/admin/servers?bucket=excluded"; }, 200);
        } catch (error) {
          setActionBusy(button, false);
          showToast("error", error.message, "error");
        }
      });
    });

    serverRefreshButton?.addEventListener("click", () => {
      if (serverRefreshButton.disabled) {
        return;
      }
      serverRefreshButton.disabled = true;
      window.location.reload();
    });

    document.querySelector("[data-open-server-modal]")?.addEventListener("click", () => {
      if (!serverModal || !serverCreateForm) {
        return;
      }
      stopBootstrapPolling();
      currentBootstrapTaskId = null;
      serverCreateForm.reset();
      writeBootstrapLog("Ожидание запуска bootstrap…");
      setTypedBootstrapInputState(null);
      setStatus(serverCreateForm.querySelector("[data-server-status]"), "");
      serverModal.hidden = false;
    });

    document.querySelector("[data-close-server-modal]")?.addEventListener("click", () => {
      if (serverModal) {
        stopBootstrapPolling();
        currentBootstrapTaskId = null;
        serverModal.hidden = true;
      }
    });

    serverModal?.addEventListener("click", (event) => {
      if (event.target === serverModal) {
        stopBootstrapPolling();
        currentBootstrapTaskId = null;
        serverModal.hidden = true;
      }
    });

    serverTypeSelect?.addEventListener("change", syncServerLocationFields);
    syncServerLocationFields();

    serverCreateForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      event.stopImmediatePropagation();
      const statusNode = serverCreateForm.querySelector("[data-server-status]");
      const formData = new FormData(serverCreateForm);
      const payload = {
        server_type: String(formData.get("server_type") || ""),
        tic_region: String(formData.get("tic_region") || "").trim() || null,
        tak_country: String(formData.get("tak_country") || "").trim() || null,
        name: String(formData.get("name") || "").trim(),
        host: String(formData.get("host") || "").trim(),
        ssh_port: Number(formData.get("ssh_port") || 22),
        ssh_login: String(formData.get("ssh_login") || "").trim(),
        ssh_password: String(formData.get("ssh_password") || ""),
      };
      try {
        stopBootstrapPolling();
        currentBootstrapTaskId = null;
        setTypedBootstrapInputState(null);
        writeBootstrapLog([
          "Подготовка bootstrap-задачи...",
          "Проверяем SSH-параметры...",
          "Ожидаем ответ Node-agent...",
        ]);
        setStatus(statusNode, "Запускаем настройку сервера...");
        const task = await requestJson("/api/admin/servers", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        renderBootstrapTask(task, statusNode);
        if (task.status === "running") {
          bootstrapPollTimer = window.setTimeout(() => pollBootstrapTask(statusNode), 1200);
        }
        return;
      } catch (error) {
        writeBootstrapLog([
          "Подключение прервано.",
          `Ошибка: ${error.message}`,
        ]);
        setStatus(statusNode, error.message, true);
      }
    }, true);

    bootstrapInputSubmit?.addEventListener("click", async () => {
      if (!currentBootstrapTaskId || !serverCreateForm) {
        return;
      }
      const statusNode = serverCreateForm.querySelector("[data-server-status]");
      const inputKind = bootstrapInput?.getAttribute("data-input-kind") || "text";
      const value = inputKind === "confirm" ? "yes" : String(bootstrapInput?.value || "");
      try {
        setStatus(statusNode, "Передаём ответ Node-agent...");
        const task = await requestJson(`/api/admin/server-bootstrap/${currentBootstrapTaskId}/input`, {
          method: "POST",
          body: JSON.stringify({ value }),
        });
        renderBootstrapTask(task, statusNode);
        if (task.status === "running") {
          bootstrapPollTimer = window.setTimeout(() => pollBootstrapTask(statusNode), 1200);
        }
      } catch (error) {
        setStatus(statusNode, error.message, true);
      }
    });

    bootstrapInput?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        bootstrapInputSubmit?.click();
      }
    });

    const submitBootstrapInput = async () => {
      if (!currentBootstrapTaskId || !serverCreateForm) {
        return;
      }
      const { input, submit } = resolveActiveBootstrapControls();
      const statusNode = serverCreateForm.querySelector("[data-server-status]");
      const inputKind = input?.getAttribute("data-input-kind") || "text";
      const value = inputKind === "confirm" ? "yes" : String(input?.value || "");
      const currentTask = {
        input_key: input?.getAttribute("data-input-key") || "",
        input_kind: inputKind,
      };
      const descriptor = describeBootstrapPrompt(currentTask);
      try {
        setActionBusy(submit, true);
        setStatus(statusNode, `${descriptor.buttonText}...`);
        const task = await requestJson(`/api/admin/server-bootstrap/${currentBootstrapTaskId}/input`, {
          method: "POST",
          body: JSON.stringify({ value }),
        });
        renderBootstrapTask(task, statusNode);
        if (task.status === "running") {
          bootstrapPollTimer = window.setTimeout(() => pollBootstrapTask(statusNode), 1200);
        }
      } catch (error) {
        setStatus(statusNode, error.message, true);
      } finally {
        setActionBusy(submit, false);
      }
    };

    [detailBootstrapInputWrap, modalBootstrapInputWrap].forEach((wrap) => {
      const submit = wrap?.querySelector("[data-bootstrap-input-submit]");
      const input = wrap?.querySelector("[data-bootstrap-input]");
      submit?.addEventListener("click", submitBootstrapInput);
      input?.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          submitBootstrapInput();
        }
      });
    });

    scheduleServerAutoRefresh();
  }
});

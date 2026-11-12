import path from "node:path";
import { expect, test } from "@playwright/test";

const widgetBundle = path.resolve("packages/widget/dist/widget.js");

test("the dashboard redirects a signed-out visitor to sign in", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await page.getByRole("link", { name: "Open dashboard", exact: true }).first().click();
  await expect(page).toHaveURL(/\/login(?:\?.*)?$/);
  await expect(page.getByRole("heading", { name: /sign in/i })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});

test("the widget supports opening, Escape and focus restoration", async ({ page }) => {
  await page.goto("/");
  await page.addScriptTag({ path: widgetBundle });
  const launcher = page.getByRole("button", { name: "Open knowledge assistant", exact: true });
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await launcher.click();
  await expect(
    page.getByRole("dialog", { name: "Knowledge assistant", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Setup preview", { exact: true }).last()).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await expect(launcher).toBeFocused();
});

test("widget greetings render as text, never executable markup", async ({ page }) => {
  await page.goto("/");
  await page.addScriptTag({ path: widgetBundle });
  const greeting = '<img src=x onerror="window.__widgetInjected=true">';
  await page.locator("aka-chat-widget").evaluate((element, value) => {
    element.setAttribute("greeting", value);
  }, greeting);
  await page.getByRole("button", { name: "Open knowledge assistant", exact: true }).click();
  await expect(page.getByText(greeting, { exact: true })).toBeVisible();
  await expect(page.locator("aka-chat-widget").locator("img")).toHaveCount(0);
});

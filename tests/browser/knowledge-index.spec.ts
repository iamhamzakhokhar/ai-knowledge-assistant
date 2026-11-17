import { randomUUID } from "node:crypto";
import { expect, test } from "@playwright/test";

test.use({ trace: "off" });

test("a team can replace and remove an automatically indexed document", async ({ page }) => {
  test.skip(!process.env.BROWSER_BASE_URL, "Requires the Compose extraction and indexing workers");
  test.setTimeout(90_000);
  const fixture = randomUUID();
  await page.goto("/signup");
  await page.getByLabel("Email", { exact: true }).fill(`index-${fixture}@example.test`);
  await page.getByLabel("Password", { exact: true }).fill(randomUUID());
  await page.getByLabel(/Team name/).fill(`Index fixture ${fixture.slice(0, 8)}`);
  await page.getByRole("button", { name: "Create account", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Knowledge index", exact: true })).toBeVisible();
  const name = "Index fixture.md";
  const document = (body: string) => ({
    name,
    mimeType: "text/markdown",
    buffer: Buffer.from(body),
  });
  await page
    .getByLabel("Document file", { exact: true })
    .setInputFiles(document("# Schedule\n\nOpen at nine."));
  await page.getByRole("button", { name: "Upload document", exact: true }).click();
  const row = page
    .getByRole("list", { name: "Indexed sources" })
    .getByRole("listitem")
    .filter({ hasText: name });
  await expect(row.getByText("Indexed", { exact: true })).toBeVisible({ timeout: 25_000 });
  const version = row.locator("code");
  const original = (await version.textContent()) ?? "";
  expect(original).toMatch(/^[\da-f-]{36}$/);

  await page.getByRole("button", { name: `Replace ${name}`, exact: true }).click();
  const replacementInput = page.getByLabel("Replacement file", { exact: true });
  await expect(replacementInput).toBeVisible();
  await replacementInput.setInputFiles(document("# Schedule\n\nOpen at ten."));
  await page.getByRole("button", { name: "Upload replacement", exact: true }).click();
  await expect(version).not.toHaveText(original, { timeout: 25_000 });
  await expect(version).toHaveText(/^[\da-f-]{36}$/);
  await expect(row.getByText("Indexed", { exact: true })).toBeVisible();

  await row.getByRole("button", { name: `Remove ${name}`, exact: true }).click();
  await row.getByRole("button", { name: `Confirm remove ${name}`, exact: true }).click();
  await expect(row.getByText("Removed", { exact: true })).toBeVisible();
  await expect(row.getByText("No active version", { exact: true })).toBeVisible();
});

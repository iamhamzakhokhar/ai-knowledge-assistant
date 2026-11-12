import { randomUUID } from "node:crypto";
import { expect, test } from "@playwright/test";

test.use({ trace: "off" });

test("documents are extracted by the worker and appear in the team dashboard", async ({ page }) => {
  test.skip(!process.env.BROWSER_BASE_URL, "Requires the real Compose worker and database");
  test.setTimeout(90_000);

  const fixture = randomUUID();
  await page.goto("/signup");
  await page.getByLabel("Email", { exact: true }).fill(`documents-${fixture}@example.test`);
  await page.getByLabel("Password", { exact: true }).fill(randomUUID());
  await page.getByLabel(/Team name/).fill(`Document fixture ${fixture.slice(0, 8)}`);
  await page.getByRole("button", { name: "Create account", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Documents", exact: true })).toBeVisible();

  const input = page.getByLabel("Document file", { exact: true });
  const uploadButton = page.getByRole("button", { name: "Upload document", exact: true });
  const name = "Fixture guide.md";
  const fixtureFile = {
    name,
    mimeType: "text/markdown",
    buffer: Buffer.from("# Service hours\n\nThe fixture desk opens at nine."),
  };
  await input.setInputFiles(fixtureFile);
  await uploadButton.click();
  const item = page.getByRole("listitem").filter({ hasText: name });
  await expect(item.getByText("Completed", { exact: true })).toBeVisible({ timeout: 25_000 });
  await item.getByRole("button", { name: `Preview ${name}`, exact: true }).click();
  await expect(page.getByRole("heading", { name: "Extracted text", exact: true })).toBeVisible();
  await expect(
    page
      .getByRole("region", { name: "Extracted text", exact: true })
      .getByText(/The fixture desk opens at nine\./),
  ).toBeVisible();

  await input.setInputFiles(fixtureFile);
  const duplicate = page.waitForResponse(
    (response) => response.url().endsWith("/documents") && response.request().method() === "POST",
  );
  await uploadButton.click();
  expect((await duplicate).status()).toBe(200);
  await expect(item).toHaveCount(1);

  const brokenName = "Unreadable fixture.pdf";
  await input.setInputFiles({
    name: brokenName,
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.7\n1 0 obj\nendobj\n%%EOF\n"),
  });
  await uploadButton.click();
  const broken = page.getByRole("listitem").filter({ hasText: brokenName });
  await expect(broken.getByText("Failed", { exact: true })).toBeVisible({ timeout: 25_000 });
  const retry = page.waitForResponse(
    (response) => response.url().endsWith("/retry") && response.request().method() === "POST",
  );
  await broken.getByRole("button", { name: `Retry ${brokenName}`, exact: true }).click();
  const retried = await retry;
  expect(retried.status()).toBe(200);
  expect((await retried.json()).attempt).toBe(2);
  await expect(broken.getByText("Failed", { exact: true })).toBeVisible({ timeout: 25_000 });
});

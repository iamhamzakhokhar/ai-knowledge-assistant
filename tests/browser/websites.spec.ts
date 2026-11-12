import { randomUUID } from "node:crypto";
import { expect, test } from "@playwright/test";

test.use({ trace: "off" });

test("the crawl worker reports a blocked destination and allows a controlled recrawl", async ({
  page,
}) => {
  test.skip(
    !process.env.BROWSER_BASE_URL || process.env.WEBSITE_FIXTURE_ENABLED !== "1",
    "Requires the disposable Compose crawler fixture",
  );
  test.setTimeout(90_000);
  const fixture = randomUUID();
  await page.goto("/signup");
  await page.getByLabel("Email", { exact: true }).fill(`website-${fixture}@example.test`);
  await page.getByLabel("Password", { exact: true }).fill(randomUUID());
  await page.getByLabel(/Team name/).fill(`Website fixture ${fixture.slice(0, 8)}`);
  await page.getByRole("button", { name: "Create account", exact: true }).click();

  const url = "https://crawl-fixture.invalid/";
  await page.getByRole("textbox", { name: "Website URL", exact: true }).fill(url);
  const submission = page.waitForResponse(
    (response) => response.url().endsWith("/websites") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Add website", exact: true }).click();
  const created = await submission;
  expect(created.status()).toBe(201);
  const { id } = await created.json();
  const website = page.getByRole("listitem").filter({ hasText: url });
  await expect(website.getByText("Failed", { exact: true })).toBeVisible({ timeout: 25_000 });
  const detail = await page.request.get(`${created.url()}/${id}`);
  expect(detail.ok()).toBe(true);
  const failure = await detail.json();
  expect(failure.pages).toEqual([]);
  expect([
    failure.error,
    ...failure.last_attempt_pages.map((entry: { error: string }) => entry.error),
  ]).toContain("blocked_destination");

  const recrawl = page.waitForResponse(
    (response) => response.url().endsWith("/recrawl") && response.request().method() === "POST",
  );
  await website.getByRole("button", { name: `Recrawl ${url}`, exact: true }).click();
  const retried = await recrawl;
  expect(retried.status()).toBe(200);
  expect((await retried.json()).attempt).toBe(2);
  await expect
    .poll(
      async () => {
        const result = await page.request.get(`${created.url()}/${id}`);
        const attempt = await result.json();
        return [attempt.attempt, attempt.status];
      },
      { timeout: 25_000 },
    )
    .toEqual([2, "failed"]);
});

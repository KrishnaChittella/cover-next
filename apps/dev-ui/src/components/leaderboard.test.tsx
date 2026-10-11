import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { current } from "@/test/bundle";

import { Leaderboard } from "./leaderboard";
import { UnrankedList } from "./unranked-list";

const board = current();

function tableRows() {
  const table = screen.getByTestId("leaderboard-table");
  return within(table).getAllByRole("row").slice(1); // without the header row
}

const namesInTable = () => tableRows().map((row) => within(row).getByRole("link").textContent);

describe("the leaderboard", () => {
  it("lists every ranked game in the artifact's rank order by default", () => {
    render(<Leaderboard entries={board.entries} />);
    expect(namesInTable()).toEqual(board.entries.map((entry) => entry.name));
    const first = tableRows()[0]!;
    const top = board.entries[0]!;
    expect(within(first).getByRole("link")).toHaveAttribute("href", `/games/${top.slug}`);
    expect(first).toHaveTextContent(String(top.rank));
    expect(first).toHaveTextContent(top.score.toFixed(1));
    expect(first).toHaveTextContent(top.summary);
    expect(first).toHaveTextContent(/medium/i); // the confidence band is written, not only coloured
    expect(first).toHaveTextContent(/current/i); // source freshness
    expect(screen.getByRole("status")).toHaveTextContent(
      `Showing ${board.entries.length} of ${board.entries.length} ranked games, in artifact rank order.`,
    );
  });

  it("shows the same rows in the narrow-screen list", () => {
    render(<Leaderboard entries={board.entries} />);
    const items = within(screen.getByTestId("leaderboard-list")).getAllByRole("listitem");
    expect(items.map((item) => within(item).getByRole("link").textContent)).toEqual(
      board.entries.map((entry) => entry.name),
    );
  });

  it("marks the sorted column for assistive technology", async () => {
    const user = userEvent.setup();
    render(<Leaderboard entries={board.entries} />);
    const header = (name: string) => screen.getByRole("columnheader", { name: new RegExp(name) });
    expect(header("Rank")).toHaveAttribute("aria-sort", "ascending");
    expect(header("Score")).toHaveAttribute("aria-sort", "none");
    await user.click(within(header("Game")).getByRole("button"));
    expect(header("Game")).toHaveAttribute("aria-sort", "ascending");
    expect(header("Rank")).toHaveAttribute("aria-sort", "none");
    const sorted = [...board.entries].sort((a, b) => a.name.localeCompare(b.name)).map((entry) => entry.name);
    expect(namesInTable()).toEqual(sorted);
    await user.click(within(header("Game")).getByRole("button"));
    expect(header("Game")).toHaveAttribute("aria-sort", "descending");
    expect(namesInTable()).toEqual([...sorted].reverse());
  });

  it("searches by name and says how many are shown", async () => {
    const user = userEvent.setup();
    render(<Leaderboard entries={board.entries} />);
    await user.type(screen.getByRole("searchbox", { name: "Search" }), "grand theft");
    const expected = board.entries.filter((entry) => entry.name.toLowerCase().includes("grand theft"));
    expect(expected.length).toBeGreaterThan(0);
    expect(namesInTable()).toEqual(expected.map((entry) => entry.name));
    expect(screen.getByRole("status")).toHaveTextContent(`Showing ${expected.length} of ${board.entries.length}`);
  });

  it("filters by lifecycle and by confidence band", async () => {
    const user = userEvent.setup();
    render(<Leaderboard entries={board.entries} />);
    await user.selectOptions(screen.getByRole("combobox", { name: "Lifecycle" }), "LIVE_SERVICE");
    const live = board.entries.filter((entry) => entry.lifecycle_state === "LIVE_SERVICE");
    expect(namesInTable()).toEqual(live.map((entry) => entry.name));
    await user.selectOptions(screen.getByRole("combobox", { name: "Confidence" }), "medium");
    expect(namesInTable()).toEqual(live.map((entry) => entry.name));
  });

  it("says so when nothing matches, and Reset brings everything back", async () => {
    const user = userEvent.setup();
    render(<Leaderboard entries={board.entries} />);
    await user.type(screen.getByRole("searchbox", { name: "Search" }), "no such game");
    expect(screen.queryByTestId("leaderboard-table")).not.toBeInTheDocument();
    expect(screen.getByText("No ranked game matches these filters.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Reset" }));
    expect(namesInTable()).toEqual(board.entries.map((entry) => entry.name));
  });

  it("offers only lifecycle states and bands that are present", () => {
    render(<Leaderboard entries={board.entries} />);
    const options = within(screen.getByRole("combobox", { name: "Confidence" })).getAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual(["All", "Medium"]);
  });

  it("explains an empty leaderboard instead of showing an empty table", () => {
    render(<Leaderboard entries={[]} />);
    expect(screen.getByText(/No game is ranked in this run/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});

describe("unranked games", () => {
  it("lists every unranked game with the reason the run recorded", () => {
    render(<UnrankedList entries={board.unranked} />);
    const items = within(screen.getByTestId("unranked-list")).getAllByRole("listitem");
    expect(items).toHaveLength(board.unranked.length);
    board.unranked.forEach((entry, index) => {
      const item = items[index]!;
      expect(within(item).getByRole("link", { name: entry.name })).toHaveAttribute("href", `/games/${entry.slug}`);
      expect(item).toHaveTextContent(entry.summary);
      expect(item).toHaveTextContent("No observations");
    });
  });

  it("distinguishes a failed collection from a game with no mapping", () => {
    render(<UnrankedList entries={board.unranked} />);
    const item = (slug: string) =>
      screen.getByRole("link", { name: board.unranked.find((entry) => entry.slug === slug)!.name }).closest("li")!;
    expect(item("fivem")).toHaveTextContent("Not mapped");
    expect(item("rust")).toHaveTextContent("Collection failed");
  });

  it("never shows a score or a rank for an unranked game", () => {
    render(<UnrankedList entries={board.unranked} />);
    const list = screen.getByTestId("unranked-list");
    expect(list).not.toHaveTextContent(/score v0/i);
    expect(list).not.toHaveTextContent(/rank \d/i);
    // Game names carry digits ("Dota 2"); a score would be a decimal, and there is none.
    expect(list.textContent).not.toMatch(/\d\.\d/);
  });

  it("says so when every game is scored", () => {
    render(<UnrankedList entries={[]} />);
    expect(screen.getByText("Every game in this run has a score.")).toBeInTheDocument();
  });
});

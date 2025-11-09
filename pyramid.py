"""Pyramid (Match-13) Solitaire implementation using pygame.

Controls:
- Click exposed cards to select/deselect them. Select two cards summing to 13 to remove them.
- Click a King to remove it immediately.
- Click the stock pile to draw a card onto the waste.
- Buttons on the right: Undo last action, Redeal (if enabled), and New Game with current seed.
- Close the window or press Escape to exit.

The game automatically downloads public-domain card images on the first run and caches them
under ``assets/cards``.
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

ASSETS_DIR = Path(__file__).resolve().parent / "assets" / "cards"
MAX_REDEALS_DEFAULT = 0
WINDOW_DEFAULT = (900, 700)
CARD_WIDTH = 90
CARD_HEIGHT = 130
CARD_SPACING_X = 24
CARD_SPACING_Y = 34
BUTTON_WIDTH = 150
BUTTON_HEIGHT = 40
HUD_FONT_SIZE = 20
MESSAGE_DURATION = 1.5

try:
    import pygame
except ImportError:  # pragma: no cover - environment dependant
    print(
        "pygame is required to run this game. Install it with 'pip install pygame'.",
        file=sys.stderr,
    )
    sys.exit(1)

try:
    import requests
except ImportError:  # pragma: no cover - optional dependency
    requests = None  # type: ignore

import io
import zipfile
import shutil
import urllib.request


RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
SUITS = ["C", "D", "H", "S"]
VALUES = {"A": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9, "10": 10, "J": 11, "Q": 12, "K": 13}


@dataclass(frozen=True)
class Card:
    """Representation of a single playing card."""

    rank: str
    suit: str

    @property
    def value(self) -> int:
        return VALUES[self.rank]

    @property
    def key(self) -> Tuple[str, str]:
        return self.rank, self.suit

    def __str__(self) -> str:  # pragma: no cover - debug helper
        return f"{self.rank}{self.suit}"


class Deck:
    """Standard 52-card deck."""

    def __init__(self, seed: Optional[int] = None) -> None:
        self.random = random.Random(seed)
        self.cards: List[Card] = [Card(rank, suit) for suit in SUITS for rank in RANKS]
        self.shuffle()

    def shuffle(self) -> None:
        self.random.shuffle(self.cards)

    def deal(self) -> Card:
        return self.cards.pop()


class Pyramid:
    """Seven-row pyramid of cards."""

    def __init__(self, cards: Sequence[Card]):
        self.rows: List[List[Optional[Card]]] = []
        iterator = iter(cards)
        for row_length in range(1, 8):
            row: List[Optional[Card]] = []
            for _ in range(row_length):
                row.append(next(iterator, None))
            self.rows.append(row)
        self.removed = [[False for _ in row] for row in self.rows]

    def card_at(self, row: int, col: int) -> Optional[Card]:
        return self.rows[row][col]

    def remove_card(self, row: int, col: int) -> Optional[Card]:
        card = self.rows[row][col]
        if card is not None:
            self.rows[row][col] = None
            self.removed[row][col] = True
        return card

    def restore_card(self, row: int, col: int, card: Card) -> None:
        self.rows[row][col] = card
        self.removed[row][col] = False

    def is_exposed(self, row: int, col: int) -> bool:
        if self.rows[row][col] is None:
            return False
        if row == len(self.rows) - 1:
            return True
        return self.rows[row + 1][col] is None and self.rows[row + 1][col + 1] is None

    def all_cards(self) -> Iterable[Tuple[int, int, Optional[Card]]]:
        for r, row in enumerate(self.rows):
            for c, card in enumerate(row):
                yield r, c, card


@dataclass
class Move:
    type: str
    payload: dict


class GameState:
    """Complete state for Pyramid Solitaire."""

    def __init__(self, seed: Optional[int], max_redeals: int):
        self.seed = seed
        self.max_redeals = max_redeals
        self.history: List[Move] = []
        self.reset()

    def reset(self) -> None:
        deck = Deck(self.seed)
        pyramid_cards = [deck.deal() for _ in range(28)]
        self.pyramid = Pyramid(pyramid_cards)
        self.stock: List[Card] = deck.cards[:]  # remaining 24 cards (deck.cards already shuffled)
        self.waste: List[Card] = []
        self.removed_count = 0
        self.redeals_used = 0
        self.history.clear()

    def card_exposed(self, location: Tuple[str, int, int]) -> bool:
        loc_type, a, b = location
        if loc_type == "pyramid":
            return self.pyramid.is_exposed(a, b)
        if loc_type == "waste":
            return bool(self.waste) and a == len(self.waste) - 1
        return False

    def remove_pyramid_card(self, row: int, col: int) -> Optional[Card]:
        card = self.pyramid.remove_card(row, col)
        if card:
            self.removed_count += 1
        return card

    def restore_pyramid_card(self, row: int, col: int, card: Card) -> None:
        self.pyramid.restore_card(row, col, card)
        self.removed_count -= 1

    def draw(self) -> bool:
        if not self.stock:
            return False
        card = self.stock.pop()
        self.waste.append(card)
        self.history.append(Move("draw", {"card": card}))
        return True

    def undo_draw(self, card: Card) -> None:
        assert self.waste and self.waste[-1] == card
        self.waste.pop()
        self.stock.append(card)

    def remove_king(self, location: Tuple[str, int, int]) -> bool:
        loc_type, a, b = location
        if loc_type == "pyramid":
            card = self.pyramid.card_at(a, b)
            if card and card.value == 13 and self.pyramid.is_exposed(a, b):
                self.remove_pyramid_card(a, b)
                self.history.append(Move("remove_king_pyramid", {"row": a, "col": b, "card": card}))
                return True
        elif loc_type == "waste" and self.waste:
            if self.waste[-1].value == 13:
                card = self.waste.pop()
                self.history.append(Move("remove_king_waste", {"card": card}))
                return True
        return False

    def remove_pair(
        self,
        first: Tuple[str, int, int],
        second: Tuple[str, int, int],
    ) -> bool:
        if first == second:
            return False
        card_a = self.get_card(first)
        card_b = self.get_card(second)
        if not card_a or not card_b:
            return False
        if card_a.value + card_b.value != 13:
            return False
        if not self.card_exposed(first) or not self.card_exposed(second):
            return False
        removed_cards: List[Tuple[str, int, int, Card]] = []
        for location in (first, second):
            loc_type, a, b = location
            if loc_type == "pyramid":
                card = self.remove_pyramid_card(a, b)
                if card:
                    removed_cards.append((loc_type, a, b, card))
            elif loc_type == "waste" and self.waste:
                card = self.waste.pop()
                removed_cards.append((loc_type, a, b, card))
        if len(removed_cards) != 2:
            for loc_type, a, b, card in reversed(removed_cards):
                if loc_type == "pyramid":
                    self.restore_pyramid_card(a, b, card)
                elif loc_type == "waste":
                    self.waste.append(card)
            return False
        self.history.append(Move("remove_pair", {"cards": removed_cards}))
        return True

    def get_card(self, location: Tuple[str, int, int]) -> Optional[Card]:
        loc_type, a, b = location
        if loc_type == "pyramid":
            return self.pyramid.card_at(a, b)
        if loc_type == "waste":
            if self.waste and a == len(self.waste) - 1:
                return self.waste[-1]
        return None

    def undo(self) -> bool:
        if not self.history:
            return False
        move = self.history.pop()
        if move.type == "draw":
            self.undo_draw(move.payload["card"])
        elif move.type == "remove_king_pyramid":
            self.restore_pyramid_card(move.payload["row"], move.payload["col"], move.payload["card"])
        elif move.type == "remove_king_waste":
            self.waste.append(move.payload["card"])
        elif move.type == "remove_pair":
            cards: List[Tuple[str, int, int, Card]] = move.payload["cards"]
            for loc_type, a, b, card in reversed(cards):
                if loc_type == "pyramid":
                    self.restore_pyramid_card(a, b, card)
                elif loc_type == "waste":
                    self.waste.append(card)
        elif move.type == "redeal":
            self.stock = move.payload["stock_before"]
            self.waste = move.payload["waste_before"]
            self.redeals_used -= 1
        return True

    def redeal(self) -> bool:
        if self.redeals_used >= self.max_redeals:
            return False
        if not self.waste:
            return False
        stock_before = self.stock[:]
        waste_before = self.waste[:]
        self.stock = self.stock[:] + list(reversed(self.waste))
        self.waste.clear()
        self.redeals_used += 1
        self.history.append(
            Move(
                "redeal",
                {"stock_before": stock_before, "waste_before": waste_before},
            )
        )
        return True

    def has_won(self) -> bool:
        return self.removed_count >= 28

    def legal_moves_remaining(self) -> bool:
        # Check stock or redeals
        if self.stock or (self.waste and self.redeals_used < self.max_redeals):
            return True
        exposed_cards = []
        for r, c, card in self.pyramid.all_cards():
            if card and self.pyramid.is_exposed(r, c):
                if card.value == 13:
                    return True
                exposed_cards.append(card.value)
        if self.waste:
            card = self.waste[-1]
            if card.value == 13:
                return True
            exposed_cards.append(card.value)
        # Check pairs summing to 13
        values = exposed_cards
        for i, val in enumerate(values):
            for j in range(i + 1, len(values)):
                if val + values[j] == 13:
                    return True
        return False


class AssetsManager:
    """Ensures card image assets are available and provides pygame surfaces."""

    def __init__(self, target_dir: Path) -> None:
        self.target_dir = target_dir
        self.card_images: Dict[Tuple[str, str], pygame.Surface] = {}
        self.back_image: Optional[pygame.Surface] = None

    def ensure_assets(self) -> None:
        if self.target_dir.exists() and (self.target_dir / "AS.png").exists() and (self.target_dir / "back.png").exists():
            return
        self.target_dir.mkdir(parents=True, exist_ok=True)
        attempts = [self._download_and_prepare_kenney, self._download_and_prepare_byron]
        for attempt in attempts:
            try:
                attempt()
                return
            except Exception:
                shutil.rmtree(self.target_dir, ignore_errors=True)
                self.target_dir.mkdir(parents=True, exist_ok=True)
                continue
        try:
            self._generate_placeholder_assets()
        except Exception as exc:
            raise RuntimeError(
                "Failed to prepare card assets. Please install pygame with font support or connect to the internet and try again."
            ) from exc

    def _download_zip(self, url: str) -> bytes:
        if requests is not None:
            response = requests.get(url, timeout=60)
            response.raise_for_status()
            return response.content
        with urllib.request.urlopen(url, timeout=60) as resp:
            return resp.read()

    def _download_and_prepare_kenney(self) -> None:
        url = "https://github.com/kenneyNL/playing-cards-pack/archive/refs/heads/master.zip"
        data = self._download_zip(url)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            tmp_dir = Path(self.target_dir.parent) / "_kenney_tmp"
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir)
            tmp_dir.mkdir(parents=True)
            try:
                zf.extractall(tmp_dir)
                base = next(tmp_dir.glob("playing-cards-pack-*/PNG"))
                cards_dir = base / "Cards"
                backs_dir = base / "Backs"
                suit_names = {"C": "Clubs", "D": "Diamonds", "H": "Hearts", "S": "Spades"}
                rank_names = {
                    "A": "A",
                    "2": "2",
                    "3": "3",
                    "4": "4",
                    "5": "5",
                    "6": "6",
                    "7": "7",
                    "8": "8",
                    "9": "9",
                    "10": "10",
                    "J": "J",
                    "Q": "Q",
                    "K": "K",
                }
                for suit, suit_name in suit_names.items():
                    for rank, rank_name in rank_names.items():
                        filename = f"card{suit_name}{rank_name}.png"
                        source = cards_dir / filename
                        if not source.exists():
                            raise FileNotFoundError(f"Missing expected card image {filename}")
                        dest = self.target_dir / f"{rank}{suit}.png"
                        shutil.copyfile(source, dest)
                back_source = backs_dir / "cardBack_blue2.png"
                if not back_source.exists():
                    back_source = next(backs_dir.glob("cardBack_*.png"))
                shutil.copyfile(back_source, self.target_dir / "back.png")
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    def _download_and_prepare_byron(self) -> None:
        url = "https://github.com/byronknoll/playing-cards/archive/refs/heads/master.zip"
        data = self._download_zip(url)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            tmp_dir = Path(self.target_dir.parent) / "_byron_tmp"
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir)
            tmp_dir.mkdir(parents=True)
            try:
                zf.extractall(tmp_dir)
                base = next(tmp_dir.glob("playing-cards-*/png/1x"))
                rank_names = {
                    "A": "ace",
                    "2": "2",
                    "3": "3",
                    "4": "4",
                    "5": "5",
                    "6": "6",
                    "7": "7",
                    "8": "8",
                    "9": "9",
                    "10": "10",
                    "J": "jack",
                    "Q": "queen",
                    "K": "king",
                }
                suit_names = {"C": "clubs", "D": "diamonds", "H": "hearts", "S": "spades"}
                for suit, suit_name in suit_names.items():
                    for rank, rank_name in rank_names.items():
                        filename = f"{rank_name}_of_{suit_name}.png"
                        source = base / filename
                        if not source.exists():
                            raise FileNotFoundError(f"Missing expected card image {filename}")
                        dest = self.target_dir / f"{rank}{suit}.png"
                        shutil.copyfile(source, dest)
                back_candidates = list((base.parent / "back").glob("*.png"))
                if not back_candidates:
                    raise FileNotFoundError("No card back image found in Byron pack")
                shutil.copyfile(back_candidates[0], self.target_dir / "back.png")
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    def _generate_placeholder_assets(self) -> None:
        needs_quit = False
        needs_font_quit = False
        if not pygame.get_init():
            pygame.init()
            needs_quit = True
        if not pygame.font.get_init():
            pygame.font.init()
            needs_font_quit = True
        try:
            card_size = (CARD_WIDTH, CARD_HEIGHT)
            background_color = (240, 240, 240)
            border_color = (20, 20, 20)
            suit_symbols = {"C": "♣", "D": "♦", "H": "♥", "S": "♠"}
            suit_colors = {"C": (0, 0, 0), "S": (0, 0, 0), "D": (180, 0, 0), "H": (180, 0, 0)}
            rank_font = pygame.font.SysFont("arial", 40, bold=True)
            suit_font = pygame.font.SysFont("arial", 36)
            center_font = pygame.font.SysFont("arial", 64, bold=True)

            for rank in RANKS:
                for suit in SUITS:
                    surface = pygame.Surface(card_size, pygame.SRCALPHA)
                    surface.fill(background_color)
                    pygame.draw.rect(surface, border_color, surface.get_rect(), 4, border_radius=8)
                    color = suit_colors[suit]
                    symbol = suit_symbols[suit]
                    rank_text = rank_font.render(rank, True, color)
                    suit_text = suit_font.render(symbol, True, color)
                    surface.blit(rank_text, (10, 8))
                    surface.blit(suit_text, (10, 50))
                    center_text = center_font.render(symbol, True, color)
                    center_rect = center_text.get_rect(center=(card_size[0] // 2, card_size[1] // 2))
                    surface.blit(center_text, center_rect)
                    pygame.image.save(surface, str(self.target_dir / f"{rank}{suit}.png"))

            back_surface = pygame.Surface(card_size, pygame.SRCALPHA)
            back_surface.fill((30, 60, 120))
            pygame.draw.rect(back_surface, border_color, back_surface.get_rect(), 4, border_radius=8)
            pattern_color = (200, 200, 255)
            for x in range(0, card_size[0], 14):
                pygame.draw.rect(back_surface, pattern_color, (x, 0, 6, card_size[1]))
            overlay_color = (20, 40, 90, 120)
            overlay = pygame.Surface(card_size, pygame.SRCALPHA)
            for y in range(0, card_size[1], 14):
                pygame.draw.rect(overlay, overlay_color, (0, y, card_size[0], 6))
            back_surface.blit(overlay, (0, 0))
            pygame.image.save(back_surface, str(self.target_dir / "back.png"))
        finally:
            if needs_font_quit:
                pygame.font.quit()
            if needs_quit:
                pygame.quit()

    def load_images(self) -> None:
        self.ensure_assets()
        for rank in RANKS:
            for suit in SUITS:
                path = self.target_dir / f"{rank}{suit}.png"
                image = pygame.image.load(str(path)).convert_alpha()
                image = pygame.transform.smoothscale(image, (CARD_WIDTH, CARD_HEIGHT))
                self.card_images[(rank, suit)] = image
        back_path = self.target_dir / "back.png"
        back_image = pygame.image.load(str(back_path)).convert_alpha()
        self.back_image = pygame.transform.smoothscale(back_image, (CARD_WIDTH, CARD_HEIGHT))

    def get_surface(self, card: Card) -> pygame.Surface:
        return self.card_images[card.key]

    def get_back_surface(self) -> pygame.Surface:
        assert self.back_image is not None
        return self.back_image


@dataclass
class SelectedCard:
    location: Tuple[str, int, int]


class Renderer:
    """Handles drawing the game state."""

    def __init__(self, screen: pygame.Surface, assets: AssetsManager):
        self.screen = screen
        self.assets = assets
        self.font = pygame.font.SysFont("arial", HUD_FONT_SIZE)
        self.message: Optional[str] = None
        self.message_until: float = 0.0
        self.button_font = pygame.font.SysFont("arial", 22)
        self.buttons: Dict[str, pygame.Rect] = {}
        self.background_color = (16, 96, 64)

    def set_screen(self, screen: pygame.Surface) -> None:
        self.screen = screen

    def show_message(self, text: str) -> None:
        self.message = text
        self.message_until = time.time() + MESSAGE_DURATION

    def update_buttons(self, width: int, height: int) -> None:
        x = width - BUTTON_WIDTH - 20
        y = 120
        self.buttons = {}
        for name in ["Undo", "Redeal", "New Game"]:
            rect = pygame.Rect(x, y, BUTTON_WIDTH, BUTTON_HEIGHT)
            self.buttons[name] = rect
            y += BUTTON_HEIGHT + 10

    def draw(self, state: GameState, selection: Optional[SelectedCard]) -> None:
        width, height = self.screen.get_size()
        self.screen.fill(self.background_color)
        self.update_buttons(width, height)
        self.draw_pyramid(state, selection)
        self.draw_stock_and_waste(state, selection)
        self.draw_hud(state)
        self.draw_buttons(state)
        self.draw_message()
        pygame.display.flip()

    def draw_pyramid(self, state: GameState, selection: Optional[SelectedCard]) -> None:
        width, _ = self.screen.get_size()
        start_x = width // 2 - CARD_WIDTH // 2
        start_y = 120
        for row_index, row in enumerate(state.pyramid.rows):
            row_width = CARD_WIDTH + (CARD_WIDTH + CARD_SPACING_X) * (row_index)
            offset_x = start_x - row_width // 2
            y = start_y + row_index * (CARD_HEIGHT + CARD_SPACING_Y)
            for col_index, card in enumerate(row):
                x = offset_x + col_index * (CARD_WIDTH + CARD_SPACING_X)
                rect = pygame.Rect(x, y, CARD_WIDTH, CARD_HEIGHT)
                if card is None:
                    continue
                image = self.assets.get_surface(card)
                self.screen.blit(image, rect)
                if selection and selection.location == ("pyramid", row_index, col_index):
                    self._draw_highlight(rect)
                elif state.pyramid.is_exposed(row_index, col_index):
                    pygame.draw.rect(self.screen, (255, 255, 255), rect, 1)

    def draw_stock_and_waste(self, state: GameState, selection: Optional[SelectedCard]) -> None:
        start_x = 80
        y = 120
        stock_rect = pygame.Rect(start_x, y, CARD_WIDTH, CARD_HEIGHT)
        if state.stock:
            self.screen.blit(self.assets.get_back_surface(), stock_rect)
        else:
            pygame.draw.rect(self.screen, (50, 70, 50), stock_rect, 2)
        pygame.draw.rect(self.screen, (0, 0, 0), stock_rect, 2)

        waste_rect = pygame.Rect(start_x + CARD_WIDTH + CARD_SPACING_X, y, CARD_WIDTH, CARD_HEIGHT)
        if state.waste:
            card = state.waste[-1]
            self.screen.blit(self.assets.get_surface(card), waste_rect)
            if selection and selection.location == ("waste", len(state.waste) - 1, 0):
                self._draw_highlight(waste_rect)
            else:
                pygame.draw.rect(self.screen, (255, 255, 255), waste_rect, 1)
        else:
            pygame.draw.rect(self.screen, (50, 70, 50), waste_rect, 2)
        pygame.draw.rect(self.screen, (0, 0, 0), waste_rect, 2)

    def draw_hud(self, state: GameState) -> None:
        width, _ = self.screen.get_size()
        text = (
            f"Stock: {len(state.stock)} | Waste: {state.waste[-1] if state.waste else 'Empty'} | "
            f"Removed: {state.removed_count}/28 | Redeals: {state.redeals_used}/{state.max_redeals} | "
            f"Seed: {state.seed if state.seed is not None else 'random'}"
        )
        surface = self.font.render(text, True, (255, 255, 255))
        self.screen.blit(surface, (40, 40))

    def draw_buttons(self, state: GameState) -> None:
        for name, rect in self.buttons.items():
            color = (80, 80, 80)
            pygame.draw.rect(self.screen, color, rect)
            pygame.draw.rect(self.screen, (0, 0, 0), rect, 2)
            text = self.button_font.render(name, True, (255, 255, 255))
            text_rect = text.get_rect(center=rect.center)
            self.screen.blit(text, text_rect)

    def draw_message(self) -> None:
        if self.message and time.time() < self.message_until:
            surface = self.font.render(self.message, True, (255, 220, 0))
            rect = surface.get_rect(center=(self.screen.get_width() // 2, 80))
            self.screen.blit(surface, rect)
        elif self.message and time.time() >= self.message_until:
            self.message = None

    def _draw_highlight(self, rect: pygame.Rect) -> None:
        pygame.draw.rect(self.screen, (255, 215, 0), rect, 4)


class Game:
    def __init__(self, state: GameState, renderer: Renderer):
        self.state = state
        self.renderer = renderer
        self.selection: Optional[SelectedCard] = None
        self.running = True
        self.last_click_time = 0.0
        self.game_over = False

    def run(self) -> None:
        clock = pygame.time.Clock()
        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    self.running = False
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    self.handle_click(event.pos)
                elif event.type == pygame.VIDEORESIZE:
                    self.renderer.set_screen(pygame.display.set_mode(event.size, pygame.RESIZABLE))
            self.renderer.draw(self.state, self.selection)
            self.check_end_conditions()
            clock.tick(60)

    def handle_click(self, position: Tuple[int, int]) -> None:
        if time.time() - self.last_click_time < 0.1:
            return
        self.last_click_time = time.time()
        for name, rect in self.renderer.buttons.items():
            if rect.collidepoint(position):
                self.handle_button(name)
                return
        if self.handle_stock_click(position):
            return
        if self.handle_waste_click(position):
            return
        if self.handle_pyramid_click(position):
            return

    def handle_button(self, name: str) -> None:
        if name == "Undo":
            if not self.state.undo():
                self.renderer.show_message("Nothing to undo")
            else:
                self.game_over = False
                self.selection = None
        elif name == "Redeal":
            if not self.state.redeal():
                self.renderer.show_message("Redeal unavailable")
            else:
                self.game_over = False
                self.selection = None
        elif name == "New Game":
            self.state.reset()
            self.selection = None
            self.game_over = False
            self.renderer.show_message("New game started")

    def handle_stock_click(self, position: Tuple[int, int]) -> bool:
        start_x = 80
        y = 120
        stock_rect = pygame.Rect(start_x, y, CARD_WIDTH, CARD_HEIGHT)
        if stock_rect.collidepoint(position):
            if not self.state.draw():
                if self.state.redeal():
                    self.renderer.show_message("Redealing waste to stock")
                    self.game_over = False
                else:
                    self.renderer.show_message("Stock empty")
            else:
                self.game_over = False
            self.selection = None
            return True
        return False

    def handle_waste_click(self, position: Tuple[int, int]) -> bool:
        if not self.state.waste:
            return False
        rect = pygame.Rect(80 + CARD_WIDTH + CARD_SPACING_X, 120, CARD_WIDTH, CARD_HEIGHT)
        if rect.collidepoint(position):
            index = len(self.state.waste) - 1
            location = ("waste", index, 0)
            card = self.state.get_card(location)
            if not card:
                return True
            if card.value == 13:
                if self.state.remove_king(location):
                    self.renderer.show_message("King removed")
                    self.game_over = False
                    self.selection = None
                return True
            if self.selection and self.selection.location == location:
                self.selection = None
            elif self.selection:
                if self.state.remove_pair(self.selection.location, location):
                    self.renderer.show_message("Pair removed")
                    self.game_over = False
                else:
                    self.renderer.show_message("Invalid pair")
                self.selection = None
            else:
                if self.state.card_exposed(location):
                    self.selection = SelectedCard(location)
            return True
        return False

    def handle_pyramid_click(self, position: Tuple[int, int]) -> bool:
        width, _ = self.renderer.screen.get_size()
        start_x = width // 2 - CARD_WIDTH // 2
        start_y = 120
        for row_index, row in enumerate(self.state.pyramid.rows):
            row_width = CARD_WIDTH + (CARD_WIDTH + CARD_SPACING_X) * row_index
            offset_x = start_x - row_width // 2
            y = start_y + row_index * (CARD_HEIGHT + CARD_SPACING_Y)
            for col_index, card in enumerate(row):
                if card is None:
                    continue
                rect = pygame.Rect(
                    offset_x + col_index * (CARD_WIDTH + CARD_SPACING_X),
                    y,
                    CARD_WIDTH,
                    CARD_HEIGHT,
                )
                if rect.collidepoint(position):
                    location = ("pyramid", row_index, col_index)
                    if not self.state.pyramid.is_exposed(row_index, col_index):
                        self.renderer.show_message("Card is covered")
                        return True
                    if card.value == 13:
                        if self.state.remove_king(location):
                            self.renderer.show_message("King removed")
                            self.game_over = False
                            self.selection = None
                        return True
                    if self.selection and self.selection.location == location:
                        self.selection = None
                        return True
                    if self.selection:
                        if self.state.remove_pair(self.selection.location, location):
                            self.renderer.show_message("Pair removed")
                            self.game_over = False
                        else:
                            self.renderer.show_message("Invalid pair")
                        self.selection = None
                    else:
                        self.selection = SelectedCard(location)
                    return True
        return False

    def check_end_conditions(self) -> None:
        if not self.game_over and self.state.has_won():
            self.renderer.show_message("You cleared the pyramid!")
            self.game_over = True
        elif not self.game_over and not self.state.legal_moves_remaining():
            self.renderer.show_message("No more moves. You lose.")
            self.game_over = True


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Play Pyramid (Match-13) Solitaire")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for deterministic shuffles")
    parser.add_argument("--redeals", type=int, default=MAX_REDEALS_DEFAULT, help="Maximum number of redeals")
    parser.add_argument(
        "--window", type=str, default=f"{WINDOW_DEFAULT[0]}x{WINDOW_DEFAULT[1]}", help="Window size WxH"
    )
    return parser.parse_args(argv)


def parse_window_size(text: str) -> Tuple[int, int]:
    try:
        width_str, height_str = text.lower().split("x")
        width = int(width_str)
        height = int(height_str)
        return max(width, 640), max(height, 480)
    except Exception as exc:
        raise argparse.ArgumentTypeError(f"Invalid window size '{text}'. Use format WxH.") from exc


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        window_size = parse_window_size(args.window)
    except argparse.ArgumentTypeError as exc:
        print(exc, file=sys.stderr)
        return 2
    pygame.init()
    assets = AssetsManager(ASSETS_DIR)
    try:
        assets.ensure_assets()
    except Exception as exc:
        print(exc, file=sys.stderr)
        pygame.quit()
        return 1
    screen = pygame.display.set_mode(window_size, pygame.RESIZABLE)
    pygame.display.set_caption("Pyramid (Match-13) Solitaire")
    assets.load_images()
    state = GameState(seed=args.seed, max_redeals=args.redeals)
    renderer = Renderer(screen, assets)
    game = Game(state, renderer)
    game.run()
    pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())

import time
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.remote.webelement import WebElement
from selenium.common.exceptions import JavascriptException
from selenium import webdriver
from functools import partial
import math
import random
import numpy as np
from typing import Callable

Point = tuple[int, int]

DEFAULT_INTERMEDIATE_RADIUS_INTERVAL = (20, 40)
DEFAULT_DEVIATION_INTERVAL = (1, 5)
DEFAULT_DISTORTION_ZONE_TIME_LENGTH = 0.05
DEFAULT_DISTORTION_FREQUENCY = 0.15

def cubic_bezier_single_coordinate(p0: int, p1: int, p2: int, p3: int, t: float):
	first_coeff = (1-t)**3
	second_coeff = 3*t*(1-t)**2
	third_coeff = 3*(1-t)*(t**2)
	fourth_coeff = t**3

	return (
		first_coeff*p0 +
		second_coeff*p1 +
		third_coeff*p2 +
		fourth_coeff*p3
	)

def cubic_bezier(p0: Point, p1: Point, p2: Point, p3: Point, t: float) -> Point:
	return (
		(cubic_bezier_single_coordinate(p0[0], p1[0], p2[0], p3[0], t)),
		(cubic_bezier_single_coordinate(p0[1], p1[1], p2[1], p3[1], t))
	)

def random_anysign(a: int, b: int) -> int:
	result = random.randint(a, b)

	if random.randint(0, 1):
		return -result

	return result

def get_bezier_path(start: Point, end: Point, intermediate_radius_interval: tuple[int, int]=DEFAULT_INTERMEDIATE_RADIUS_INTERVAL) -> Callable[[float], Point]:
	p0, p3 = start, end

	p1 = (
		p0[0]+random_anysign(intermediate_radius_interval[0], intermediate_radius_interval[1]),
		p0[1]+random_anysign(intermediate_radius_interval[0], intermediate_radius_interval[1])
	)

	p2 = (
		p3[0]+random_anysign(intermediate_radius_interval[0], intermediate_radius_interval[1]),
		p3[1]+random_anysign(intermediate_radius_interval[0], intermediate_radius_interval[1])
	)

	return partial(cubic_bezier, p0, p1, p2, p3)

def get_distorted_bezier_path(
	start: Point,
	end: Point,
	intermediate_radius_interval: tuple[int, int]=DEFAULT_INTERMEDIATE_RADIUS_INTERVAL,
	distortion_zone_time_length: float=DEFAULT_DISTORTION_ZONE_TIME_LENGTH,
	distortion_frequency: float=DEFAULT_DISTORTION_FREQUENCY,
	deviation_interval: tuple[int, int]=DEFAULT_DEVIATION_INTERVAL
) -> Callable[[float], Point]:
	distortion_zones: list[tuple[float, float]] = [
		(i*distortion_zone_time_length, (i+1)*distortion_zone_time_length)
		for i in range(int(1/distortion_zone_time_length))
		if random.uniform(0, 1) < distortion_frequency
	]

	distortion_offsets: list[Point] = [
		(
			random_anysign(deviation_interval[0], deviation_interval[1]),
			random_anysign(deviation_interval[0], deviation_interval[1])
		)
		for _ in range(len(distortion_zones))
	]

	def get_distorted_point(
		true_point: Point,
		distortion_offset: Point,
		distortion_zone: tuple[float, float],
		t: float
	) -> Point:
		distortion_zone_length = distortion_zone[1]-distortion_zone[0]
		distortion_zone_progress = (t-distortion_zone[0])/distortion_zone_length

		if distortion_zone_progress < 0.5: # move from true to distorted point
			return (
				true_point[0]+distortion_offset[0]*distortion_zone_progress*2,
				true_point[1]+distortion_offset[1]*distortion_zone_progress*2
			)
		else: # move from distorted to true point
			return (
				true_point[0]+distortion_offset[0]*(1-(distortion_zone_progress-0.5)*2),
				true_point[1]+distortion_offset[1]*(1-(distortion_zone_progress-0.5)*2)
			)

	bezier_path = get_bezier_path(start, end, intermediate_radius_interval)

	def distored_path_function(t: float):
		true_point = bezier_path(t)

		for i, distortion_zone in enumerate(distortion_zones):
			if distortion_zone[0] <= t <= distortion_zone[1]:
				return get_distorted_point(
					true_point,
					distortion_offsets[i],
					distortion_zone,
					t
				)

		# we are not in a distortion zone, return the true point
		return true_point

	return distored_path_function

def logistic_sigmoid(x: float) -> float:
	return 2/(1+np.exp(-x)) - 1

def get_path_with_transformed_velo(
	start: Point,
	end: Point,
	intermediate_radius_interval: tuple[int, int]=DEFAULT_INTERMEDIATE_RADIUS_INTERVAL,
	distortion_zone_time_length: float=DEFAULT_DISTORTION_ZONE_TIME_LENGTH,
	distortion_frequency: float=DEFAULT_DISTORTION_FREQUENCY,
	deviation_interval: tuple[int, int]=DEFAULT_DEVIATION_INTERVAL
) -> Callable[[float], Point]:
	bezier_path = get_distorted_bezier_path(
		start,
		end,
		intermediate_radius_interval,
		distortion_zone_time_length,
		distortion_frequency,
		deviation_interval
	)

	return lambda t: bezier_path(logistic_sigmoid(t))

FITTS_LAW_A = 0.5500
FITTS_LAW_B = 0.1276

def get_final_path_from_real_time(
	movement_time: float,
	start: Point,
	end: Point,
	intermediate_radius_interval: tuple[int, int]=DEFAULT_INTERMEDIATE_RADIUS_INTERVAL,
	distortion_zone_time_length: float=DEFAULT_DISTORTION_ZONE_TIME_LENGTH,
	distortion_frequency: float=DEFAULT_DISTORTION_FREQUENCY,
	deviation_interval: tuple[int, int]=DEFAULT_DEVIATION_INTERVAL
) -> Callable[[float], Point]:
	path = get_path_with_transformed_velo(
		start,
		end,
		intermediate_radius_interval,
		distortion_zone_time_length,
		distortion_frequency,
		deviation_interval
	)

	def final_path_function(t: float) -> Point:
		if t < 0:
			return start
		elif t > movement_time:
			return end

		normalized_t = (t / movement_time)*4.5

		return path(normalized_t)

	return final_path_function

def get_movement_time_from_fitts_law(distance: float, target_width: float) -> float:
	index_of_difficulty = math.log2((2.0 * distance) / target_width)
	movement_time = FITTS_LAW_A + FITTS_LAW_B * index_of_difficulty

	return movement_time

def get_final_path_with_fitts_law(
	target_width: float,
	start: Point,
	end: Point,
	intermediate_radius_interval: tuple[int, int]=DEFAULT_INTERMEDIATE_RADIUS_INTERVAL,
	distortion_zone_time_length: float=DEFAULT_DISTORTION_ZONE_TIME_LENGTH,
	distortion_frequency: float=DEFAULT_DISTORTION_FREQUENCY,
	deviation_interval: tuple[int, int]=DEFAULT_DEVIATION_INTERVAL
) -> Callable[[float], Point]:
	distance = math.dist(start, end)
	movement_time = get_movement_time_from_fitts_law(distance, target_width)

	return get_final_path_from_real_time(
		movement_time,
		start,
		end,
		intermediate_radius_interval,
		distortion_zone_time_length,
		distortion_frequency,
		deviation_interval
	)

def choose_target_in_element(x: int, y: int, height: int, width: int) -> Point:
	# choose a random point near the center of the element

	left_bound_x = x + width * 0.25
	right_bound_x = x + width * 0.75
	top_bound_y = y + height * 0.25
	bottom_bound_y = y + height * 0.75

	return (
		random.randint(int(left_bound_x), int(right_bound_x)),
		random.randint(int(top_bound_y), int(bottom_bound_y))
	)

class MouseUtils:
	def __init__(self, driver: webdriver.Edge, debug_cursor: bool = False):
		self.driver = driver
		self.debug_cursor = debug_cursor
		self.fallback_init_pos = (200, 200) # realistic default position
		self.reinitialize()

	def reinitialize(self):
		self.init_driver_with_mouse_tracking()
		if self.debug_cursor:
			self.init_driver_with_cursor_visualization()

	def init_driver_with_mouse_tracking(self):
		initial_pos = self.fallback_init_pos

		js_tracker = f"""
	window._trackedCursorX = {int(initial_pos[0])};
	window._trackedCursorY = {int(initial_pos[1])};
	document.addEventListener('mousemove', function(event) {{
		window._trackedCursorX = event.clientX;
		window._trackedCursorY = event.clientY;
	}}, {{ passive: true }});
	"""
		try:
			self.driver.execute_script(js_tracker)
		except Exception:
			pass

	def init_driver_with_cursor_visualization(self):
		if not self.debug_cursor:
			return

		cursor_script = """
	if (!document.getElementById('debug-visual-cursor')) {
		var visualCursor = document.createElement('div');
		visualCursor.id = 'debug-visual-cursor';
		visualCursor.style.position = 'fixed';
		visualCursor.style.zIndex = '99999';
		visualCursor.style.width = '12px';
		visualCursor.style.height = '12px';
		visualCursor.style.background = 'rgba(230, 50, 50, 0.85)';
		visualCursor.style.borderRadius = '50%';
		visualCursor.style.border = '2px solid white';
		visualCursor.style.pointerEvents = 'none';
		visualCursor.style.top = '0px';
		visualCursor.style.left = '0px';
		visualCursor.style.transition = 'all 0.05s ease';
		document.body.appendChild(visualCursor);

		window.moveVisualCursor = function(x, y) {
			var cursor = document.getElementById('debug-visual-cursor');
			if (cursor) {
				cursor.style.left = x + 'px';
				cursor.style.top = y + 'px';
			}
		};
	}
	"""
		try:
			self.driver.execute_script(cursor_script)
		except Exception:
			pass

	def get_current_mouse_position(self) -> Point:
		try:
			pos = self.driver.execute_script("return { x: window._trackedCursorX, y: window._trackedCursorY };")
			if pos and pos.get('x') is not None and pos.get('y') is not None:
				x, y = int(pos['x']), int(pos['y'])
				self.fallback_init_pos = (x, y)
				return (x, y)
		except Exception:
			pass

		return self.fallback_init_pos

	def move_mouse(self, move_time: float, path_function: Callable[[float], Point], visualize: bool = False):
		start_time = time.monotonic()
		end_time = start_time + move_time

		# The distorted bezier path can overshoot the window edge, which the
		# driver rejects, so keep every sampled point inside the viewport.
		try:
			viewport = self.driver.execute_script("return [window.innerWidth, window.innerHeight];")
			max_x = max(10, int(viewport[0]) - 4) if viewport else 1360
			max_y = max(10, int(viewport[1]) - 4) if viewport else 760
		except Exception:
			max_x, max_y = 1360, 760

		while (current_time := time.monotonic()) < end_time:
			t = current_time - start_time
			point = path_function(t)

			point = (
				min(max(2, int(point[0])), max_x),
				min(max(2, int(point[1])), max_y)
			)

			try:
				actions = ActionBuilder(self.driver, duration=0)
				actions.pointer_action.move_to_location(point[0], point[1])
				actions.perform()
				self.fallback_init_pos = point
			except Exception:
				pass

			if visualize and self.debug_cursor:
				try:
					self.driver.execute_script(f"if (window.moveVisualCursor) window.moveVisualCursor({point[0]}, {point[1]});")
				except JavascriptException:
					self.reinitialize()

			# 60 Hz tick-rate (16ms) to prevent overwhelming WebDriver with HTTP requests
			time.sleep(0.016)


	def wheel_scroll_element_into_view(self, element: WebElement, max_wheel_events: int = 60):
		"""Scroll the element into the viewport with simulated wheel input.

		Wheel steps of varying size with short pauses, the way a person scrolls,
		instead of a fixed-size burst. The loop is bounded on purpose: an element
		that never fits the viewport completely, for example one taller than the
		window, must not hang the run forever. When the budget runs out the
		caller proceeds with the element as visible as it got.
		"""
		for _ in range(max_wheel_events):
			top, bottom, height = self.driver.execute_script(
				"var r = arguments[0].getBoundingClientRect();"
				"return [r.top, r.bottom, window.innerHeight];",
				element
			)

			if top >= 0 and bottom <= height:
				break

			# Aim the element at the middle of the viewport, one notch at a time.
			distance = (top + bottom) / 2 - height / 2
			step = max(-320, min(320, distance))
			step = int(step * random.uniform(0.6, 1.0))

			if abs(step) < 40:
				step = 40 if distance > 0 else -40

			ActionChains(self.driver).scroll_by_amount(0, step).perform()

			time.sleep(random.uniform(0.04, 0.12))

	def wheel_scroll_to_top(self, max_wheel_events: int = 80):
		"""Scroll back to the top of the page with simulated wheel input.

		Reads the actual scroll position instead of unwinding a counted number
		of steps, because the page height can change while cards update and a
		symmetric unwind then lands in the wrong place.
		"""
		for _ in range(max_wheel_events):
			offset = self.driver.execute_script("return window.scrollY || window.pageYOffset;")

			if offset <= 0:
				break

			step = min(340, int(offset))
			step = max(60, int(step * random.uniform(0.6, 1.0)))

			ActionChains(self.driver).scroll_by_amount(0, -step).perform()

			time.sleep(random.uniform(0.04, 0.12))

	def realistic_browse_scroll(self, min_scrolls: int = 2, max_scrolls: int = 4):
		"""Simulate authentic human browsing scroll behavior on a page.

		Performs natural variable-distance scrolls, interspersed with reading pauses,
		occasional minor back-scrolls (reading review), and natural deceleration.
		"""
		num_scrolls = random.randint(min_scrolls, max_scrolls)

		for i in range(num_scrolls):
			distance = random.randint(180, 420)
			steps = random.randint(3, 6)

			for step_idx in range(steps):
				sub_step = int((distance / steps) * random.uniform(0.7, 1.3))
				try:
					ActionChains(self.driver).scroll_by_amount(0, sub_step).perform()
				except Exception:
					pass
				time.sleep(random.uniform(0.02, 0.05))

			# Human reading pause after scroll chunk
			time.sleep(random.uniform(0.6, 1.8))

			# 25% chance of minor upward correction (re-reading behavior)
			if random.random() < 0.25:
				back_distance = random.randint(50, 140)
				try:
					ActionChains(self.driver).scroll_by_amount(0, -back_distance).perform()
				except Exception:
					pass
				time.sleep(random.uniform(0.4, 1.0))

	def move_to_element(self, element: WebElement, visualize: bool = False):
		# The pointer is moved to viewport coordinates, so an element below the
		# fold yields a target outside the window and the driver rejects the move
		# with MoveTargetOutOfBoundsException. Bring it into view first, but only
		# when it actually is out of view: unconditionally re-centering visible
		# elements is what caused the page to jump between tasks. When scrolling
		# is needed it is smooth, and since smooth scrolling is asynchronous, the
		# rect is polled until it stops moving before the path is computed.
		fully_in_view = self.driver.execute_script("""
			var r = arguments[0].getBoundingClientRect();
			return (
				r.top >= 0 && r.left >= 0 &&
				r.bottom <= (window.innerHeight || document.documentElement.clientHeight) &&
				r.right <= (window.innerWidth || document.documentElement.clientWidth)
			);
		""", element)

		if not fully_in_view:
			self.driver.execute_script(
				"arguments[0].scrollIntoView({block: 'center', inline: 'center', behavior: 'smooth'});",
				element
			)

			last_rect = None

			for _ in range(20):
				time.sleep(0.15)

				rect = self.driver.execute_script(
					"var r = arguments[0].getBoundingClientRect();"
					"return [Math.round(r.top), Math.round(r.left)];",
					element
				)

				if rect == last_rect:
					break

				last_rect = rect

		current_mouse_position = self.get_current_mouse_position()

		rect = self.driver.execute_script("""
			var rect = arguments[0].getBoundingClientRect();
			return {x: rect.left, y: rect.top, width: rect.width, height: rect.height};
		""", element)

		target_position = choose_target_in_element(
			rect['x'],
			rect['y'],
			rect['height'],
			rect['width']
		)

		move_time = get_movement_time_from_fitts_law(
			math.dist(current_mouse_position, target_position),
			(rect['width'] + rect['height']) / 2
		)

		path_fn = get_final_path_from_real_time(
			movement_time=move_time,
			start=current_mouse_position,
			end=target_position
		)

		self.move_mouse(move_time, path_fn, visualize)

	def human_like_click(self, time_interval: tuple[int, int]=(200, 300)):
		ActionChains(self.driver, duration=random.randint(time_interval[0], time_interval[1])).click().perform()
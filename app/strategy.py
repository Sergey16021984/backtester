import copy
import logging
from decimal import Decimal

from app.exchange_client.base import BaseClient
from app.models import Position, OnHoldPositions, Tick
from app.settings import app_settings

logger = logging.getLogger(__name__)


class Strategy:
    tick_history_limit: int = 10

    def __init__(self, exchange_client: BaseClient) -> None:
        self._open_positions: list[Position] = []
        self._closed_positions: list[Position] = []
        self._max_onhold_positions: OnHoldPositions | None = None
        self._ticks_history: list[Tick] = []
        self._exchange_client = exchange_client

    def get_last_tick(self) -> Tick:
        return self._get_ticks_history()[-1]

    def get_previous_tick(self) -> Tick:
        return self._get_ticks_history()[-2]

    def tick(self, tick: Tick) -> bool:
        self._push_ticks_history(tick)
        self._update_max_hold_amount(tick)

        if tick.number >= app_settings.ticks_amount_limit:
            logger.warning('end trading session by tick limit')
            return False

        if not tick.number:
            logger.info('init buy')
            for _ in range(app_settings.init_buy_amount):
                self._open_position(
                    quantity=app_settings.continue_buy_amount,
                    price=float(tick.price),
                    tick_number=tick.number,
                )
            return True

        if tick.number == 1:
            logger.info('skip')
            return True

        # check global stop loss
        if tick.price <= app_settings.global_stop_loss:
            logger.warning('global stop loss fired! open: {0}. closed: {1}'.format(
                len(self._open_positions),
                len(self._closed_positions),
            ))
            for position_for_close in self._get_open_positions_for_sell():
                self._close_position(position_for_close, price=float(tick.price), tick_number=tick.number)
            self._update_max_hold_amount(tick)
            return False

        # search position for sale
        sale_completed = self._sell_something(price=float(tick.price), tick_number=tick.number)

        # вот смотри там где разница была больше или равно 0.02 мы закупали
        if not sale_completed:
            logger.debug('try to buy something')
            self._buy_something(price=tick.price, tick_number=tick.number)

        self._update_max_hold_amount(tick)
        return True

    def show_results(self) -> None:
        buy_amount_without_current_opened = sum(
            [pos.open_rate * pos.amount for pos in self._closed_positions]
        )
        buy_without_current_opened = sum(
            [pos.amount for pos in self._closed_positions]
        )
        buy_amount_total = buy_amount_without_current_opened + sum(
            [pos.open_rate * pos.amount for pos in self._open_positions]
        )
        buy_total = buy_without_current_opened + sum(
            [pos.amount for pos in self._open_positions]
        )
        sell_amount_without_current_opened = sum(
            [pos.close_rate * pos.amount for pos in self._closed_positions]
        )
        sell_without_current_opened = sum(
            [pos.amount for pos in self._closed_positions]
        )
        liquidation_amount = sum(
            [float(self.get_last_tick().price) * pos.amount for pos in self._open_positions]
        )
        liquidation = sum(
            [pos.amount for pos in self._open_positions]
        )

        # считаем доходность относительно максимума средств в обороте
        max_amount_onhold = self._max_onhold_positions.buy_amount if self._max_onhold_positions else 0
        profit_amount_without_current_opened = sell_amount_without_current_opened - buy_amount_without_current_opened
        profit_amount_total = sell_amount_without_current_opened + liquidation_amount - buy_amount_total
        profit_percent_without_current_opened = (profit_amount_without_current_opened / max_amount_onhold * 100) if max_amount_onhold else 0
        profit_percent_total = (profit_amount_total / max_amount_onhold * 100) if max_amount_onhold else 0

        print('')
        print('')
        print('Результаты тестирования:')
        print('')
        print('Общая оборотная сумма денег с начала запуска $%.2f (%.2f монет)' % (
            buy_amount_total,
            buy_total,
        ))

        print('')
        print('Оборотная сумма денег на покупки реализованных монет $%.2f (%.2f монет)' % (
            buy_amount_without_current_opened,
            buy_without_current_opened,
        ))

        print('')
        print('Оборотная сумма денег за продажу реализованных монет $%.2f (%.2f монет)' % (
            sell_amount_without_current_opened,
            sell_without_current_opened,
        ))
        print('Доходность без учёта зависших монет: $%.2f (%.2f%%)' % (
            profit_amount_without_current_opened,
            profit_percent_without_current_opened,
        ))

        print('')
        print('Сумма денег за ликвидацию зависших монет $%.2f (%.2f монет)' % (
            liquidation_amount,
            liquidation,
        ))
        print('Доходность с учётом зависших монет: $%.2f (%.2f%%)' % (
            profit_amount_total,
            profit_percent_total,
        ))

        print('')
        print('Требуемая сумма денег для обеспечения текущего тестирования $%.2f (%.1f монет)' % (
            self._max_onhold_positions.buy_amount if self._max_onhold_positions else 0,
            self._max_onhold_positions.quantity if self._max_onhold_positions else 0,
        ))
        print('')
        print('')

    def _update_max_hold_amount(self, tick: Tick):
        on_hold_current = OnHoldPositions(
            quantity=sum([pos.amount for pos in self._open_positions]),
            buy_amount=sum([pos.amount * pos.open_rate for pos in self._open_positions]),
            tick_number=tick.number,
            tick_rate=float(tick.price),
        )
        if not self._max_onhold_positions or self._max_onhold_positions.buy_amount < on_hold_current.buy_amount:
            self._max_onhold_positions = on_hold_current

    def _get_open_positions_for_sell(self) -> list[Position]:
        return sorted(copy.deepcopy(self._open_positions), key=lambda x: x.open_rate)

    def _open_position(self, quantity: float, price: float, tick_number: int) -> bool:
        buy_response = self._exchange_client.buy(
            quantity=Decimal(quantity),
            price=Decimal(price),
        )
        logger.debug('open new position response {0}'.format(buy_response))
        if not buy_response or buy_response.get('status') != 'FILLED':
            logger.info('open new position - unsuccessfully "{0}"'.format(
                buy_response,
            ))
            return False

        logger.info('open new position')
        self._open_positions.append(Position(
            amount=float(buy_response['executedQty']),
            open_rate=float(buy_response['cummulativeQuoteQty']) / float(buy_response['executedQty']),
            open_tick_number=tick_number,
        ))
        return True

    def _close_position(self, position_for_close: Position, price: float, tick_number: int) -> bool:
        sell_response = self._exchange_client.sell(
            quantity=Decimal(position_for_close.amount),
            price=Decimal(price),
        )
        logger.debug('close position response {0}'.format(sell_response))
        if not sell_response or sell_response.get('status') != 'FILLED':
            logger.info('close position - unsuccessfully "{0}"'.format(
                sell_response,
            ))
            return False

        self._open_positions.remove(position_for_close)
        price = float(sell_response['cummulativeQuoteQty']) / float(sell_response['executedQty'])
        position_for_close.close_rate = price
        position_for_close.close_tick_number = tick_number
        self._closed_positions.append(position_for_close)
        return True

    def _sell_something(self, price: float, tick_number: int) -> bool:
        logger.debug('search position for sell. Tick price: {0}'.format(price))

        sale_completed: bool = False
        for position in self._get_open_positions_for_sell():
            logger.debug(position)

            # условия на продажу
            # - текущая цена выше цены покупки на 5%
            logger.debug('check sale by tick rate and open rate.')
            logger.debug('Position: {0}. Current price {1}. Open rate + 5%: {2}. Check {3}'.format(
                position,
                price,
                position.open_rate * app_settings.avg_rate_sell_limit,
                price >= position.open_rate * app_settings.avg_rate_sell_limit,
            ))
            if price >= position.open_rate * app_settings.avg_rate_sell_limit:
                sell_response = self._close_position(position, price=price, tick_number=tick_number)
                sale_completed = sell_response or sale_completed
                continue

        return sale_completed

    def _buy_something(self, price: Decimal, tick_number: int) -> None:
        rate_go_down = self.get_previous_tick().price - price
        logger.debug('check rates for buy. Prev rate: %.4f, diff %.4f' % (
            float(self.get_previous_tick().price),
            float(rate_go_down),
        ))
        if rate_go_down >= app_settings.step:
            self._open_position(
                quantity=app_settings.continue_buy_amount,
                price=float(price),
                tick_number=tick_number,
            )

    def _push_ticks_history(self, tick: Tick) -> None:
        if len(self._ticks_history) >= self.tick_history_limit:
            self._ticks_history.pop(0)
        self._ticks_history.append(tick)

    def _get_ticks_history(self) -> list[Tick]:
        return self._ticks_history

    def _get_history_average_price(self) -> Decimal:
        return (self._get_ticks_history()[-3].price + self.get_previous_tick().price) / 2

import logging
import os
from decimal import Decimal

from app.exchange_client.dummy import Dummy
from app.models import Tick
from app.settings import app_settings
from app.strategy import Strategy

logger = logging.getLogger(__name__)


def main() -> None:
    strategy = Strategy(exchange_client=Dummy(symbol='dummy'))
    for tick in get_rates(app_settings.rates_filename):
        logger.info('tick {0}'.format(tick))

        go_to_next_step = strategy.tick(tick=tick)
        if not go_to_next_step:
            logger.info('end trading')
            break

    strategy.show_results()


def get_rates(filename: str) -> list[Tick]:
    filepath = os.path.abspath(os.path.join(
        app_settings.rates_path,
        filename,
    ))

    output: list[Tick] = []
    with open(filepath) as fd:
        tick_number = 0
        for num, line in enumerate(fd):
            if not num or not line:
                continue

            output.append(Tick(
                number=tick_number,
                price=Decimal(line.split(',')[1]),
            ))
            tick_number += 1
    return output


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.DEBUG if app_settings.debug else logging.INFO,
        format='%(asctime)s %(levelname)-8s %(message)s',
    )
    main()

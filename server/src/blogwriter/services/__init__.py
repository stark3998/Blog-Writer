"""blogwriter.services: the integrator's runtime adapters and lifecycles (contract section 5).

Adapters (each implements a :mod:`blogwriter.domain.ports` protocol):

* :class:`PushNotifier` / :class:`NullNotifier`: the ``Notifier`` port over web push
  (``notify.webpush.WebPushNotifier``), and :class:`CosmosSubscriptionRepo`, the push
  subscriptions in the ``settings`` container (kind ``push_subscription``);
* :class:`RenderServiceAdapter`: the ``RenderService`` port over ``blogwriter.render``
  (brand kit -> ``RenderBrand``, headshot from Blob, render errors -> ``RenderFailed``);
* :class:`RouterEmbedder`: the ``Embedder`` port over ``AIRouter.embed`` (``Task.EMBED``,
  ``text-embedding-3-large`` at 1024 dimensions, ledgered and priced);
* :class:`ScheduleSeeder`: the ``ScheduleSeeder`` port (every package's schedule entries).

Lifecycles (``startup(app)`` / ``shutdown(app)``, in ``blogwriter.main``'s order):
:class:`CoreServicesLifecycle`, :class:`RenderAdapterLifecycle`, :class:`ServicesLifecycle`,
:class:`SchedulesLifecycle`. :func:`system_status` builds ``GET /api/system/status``.
"""

from blogwriter.services.embedder import EMBEDDING_DIMENSIONS, RouterEmbedder
from blogwriter.services.lifecycles import (
    CoreServicesLifecycle,
    RenderAdapterLifecycle,
    SchedulesLifecycle,
    ServicesLifecycle,
)
from blogwriter.services.notifier import NullNotifier, PushNotifier
from blogwriter.services.push_repo import PUSH_SUBSCRIPTION_KIND, CosmosSubscriptionRepo
from blogwriter.services.render import RenderServiceAdapter, render_brand
from blogwriter.services.schedules import ScheduleSeeder, SeedReport
from blogwriter.services.status import (
    ServiceView,
    StatusProbe,
    SystemStatus,
    register_status_probe,
    system_status,
)

__all__ = [
    "EMBEDDING_DIMENSIONS",
    "PUSH_SUBSCRIPTION_KIND",
    "CoreServicesLifecycle",
    "CosmosSubscriptionRepo",
    "NullNotifier",
    "PushNotifier",
    "RenderAdapterLifecycle",
    "RenderServiceAdapter",
    "RouterEmbedder",
    "ScheduleSeeder",
    "SchedulesLifecycle",
    "SeedReport",
    "ServiceView",
    "ServicesLifecycle",
    "StatusProbe",
    "SystemStatus",
    "register_status_probe",
    "render_brand",
    "system_status",
]

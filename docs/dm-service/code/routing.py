from django.urls import path

from direct_messages.consumers import DirectMessageConsumer


websocket_urlpatterns = [
    path("ws/dm/", DirectMessageConsumer.as_asgi()),
]

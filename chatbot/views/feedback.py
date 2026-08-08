from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from ..serializers import FeedbackSerializer
from ..services.feedback import FeedbackService

import logging


logger = logging.getLogger(__name__)

class FeedbackView(APIView):
    """
    Public feedback form. Open to logged-out visitors on purpose, so the abuse
    limit is enforced per caller address by DRF's throttling rather than on
    anything supplied in the request body.
    """

    authentication_classes = []
    permission_classes = []
    throttle_scope = "feedback"


    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.service = FeedbackService()

    def post(self, request):
        input_serializer = FeedbackSerializer(data=request.data)
        if not input_serializer.is_valid():
            return Response(input_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Anything raised past here is unexpected and belongs in the logs with
        # its traceback, not flattened into a 400.
        result = self.service.send_user_feedback(
            email=input_serializer.validated_data["email"],
            message=input_serializer.validated_data["message"],
        )
        return Response(result, status=status.HTTP_200_OK)

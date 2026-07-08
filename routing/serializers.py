from rest_framework import serializers


class LatLonSerializer(serializers.Serializer):
    lat = serializers.FloatField()
    lon = serializers.FloatField()


class RouteRequestSerializer(serializers.Serializer):
    """
    Accepts EITHER a free-text place ('start', 'finish') OR explicit
    coordinates ('start_lat'/'start_lon', 'finish_lat'/'finish_lon').

    Passing coordinates directly skips geocoding entirely, so the request
    only makes a single call to the routing API - the fastest and most
    call-frugal way to use this endpoint. Free-text is supported for
    convenience since it's what a human tester (e.g. via Postman) will
    usually want to type.
    """
    start = serializers.CharField(required=False, allow_blank=False)
    finish = serializers.CharField(required=False, allow_blank=False)
    start_lat = serializers.FloatField(required=False)
    start_lon = serializers.FloatField(required=False)
    finish_lat = serializers.FloatField(required=False)
    finish_lon = serializers.FloatField(required=False)

    def validate(self, data):
        has_start_coords = 'start_lat' in data and 'start_lon' in data
        has_finish_coords = 'finish_lat' in data and 'finish_lon' in data
        if not (data.get('start') or has_start_coords):
            raise serializers.ValidationError('Provide either "start" (text) or "start_lat"/"start_lon".')
        if not (data.get('finish') or has_finish_coords):
            raise serializers.ValidationError('Provide either "finish" (text) or "finish_lat"/"finish_lon".')
        return data

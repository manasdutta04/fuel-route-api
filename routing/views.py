from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status

from routing.serializers import RouteRequestSerializer
from routing.services import geocoding, osrm_client, fuel_optimizer
from routing.services.geocoding import GeocodingError
from routing.services.osrm_client import RoutingError


class RouteView(APIView):
    """
    POST /api/route/

    Body (free text):
        {"start": "Chicago, IL", "finish": "Denver, CO"}

    Body (coordinates - skips geocoding, only 1 outbound API call total):
        {"start_lat": 41.8781, "start_lon": -87.6298,
         "finish_lat": 39.7392, "finish_lon": -104.9903}

    Response:
        {
          "start": {"lat": ..., "lon": ...},
          "finish": {"lat": ..., "lon": ...},
          "total_distance_miles": 1003.4,
          "route_geometry": [[lat, lon], ...],   # for drawing the map
          "fuel_stops": [
             {"name": ..., "city": ..., "state": ..., "price_per_gallon": ...,
              "mile_marker": ..., "distance_off_route_miles": ...,
              "gallons_purchased": ..., "cost": ...}
          ],
          "total_fuel_cost": 214.87,
          "feasible": true,
          "message": null
        }
    """

    def post(self, request):
        serializer = RouteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            if 'start_lat' in data:
                start_lat, start_lon = data['start_lat'], data['start_lon']
            else:
                start_lat, start_lon = geocoding.geocode(data['start'])

            if 'finish_lat' in data:
                finish_lat, finish_lon = data['finish_lat'], data['finish_lon']
            else:
                finish_lat, finish_lon = geocoding.geocode(data['finish'])
        except GeocodingError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            route = osrm_client.get_route(start_lat, start_lon, finish_lat, finish_lon)
        except RoutingError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        result = fuel_optimizer.find_fuel_stops(route)

        response = {
            'start': {'lat': start_lat, 'lon': start_lon},
            'finish': {'lat': finish_lat, 'lon': finish_lon},
            'total_distance_miles': result['total_distance_miles'],
            'route_geometry': route['geometry'],
            'fuel_stops': [
                {
                    'name': stop.station['name'],
                    'address': stop.station['address'],
                    'city': stop.station['city'],
                    'state': stop.station['state'],
                    'price_per_gallon': float(stop.station['retail_price']),
                    'mile_marker': stop.mile_marker,
                    'distance_off_route_miles': stop.distance_off_route,
                    'gallons_purchased': stop.gallons_purchased,
                    'cost': stop.cost,
                }
                for stop in result['stops']
            ],
            'total_fuel_cost': result['total_fuel_cost'],
            'feasible': result['feasible'],
            'message': result['message'],
        }
        return Response(response, status=status.HTTP_200_OK)

from pydantic import BaseModel

class ListEventsInput(BaseModel):
    start_date: str
    end_date: str

class GetEventDetailsInput(BaseModel):
    event_id: str

class SearchEventsInput(BaseModel):
    keyword: str

class CreateEventInput(BaseModel):
    title: str
    date: str
    start_time: str
    end_time: str
    description: str = ""
    location: str = ""

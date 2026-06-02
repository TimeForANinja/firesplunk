import logging
import pymongo


def init_db(db: pymongo.database.Database):
    # Following collections are expected:
    # summaries -> list of all records
    # data_status -> status of each date
    # correlated_rule_ip -> correlated data for each IP/rule pair
    # correlated_rule_ports -> correlated data for each rule/port pair

    # Ensure indexes exist
    db['summaries'].create_index("date")
    db['summaries'].create_index("expires_at", expireAfterSeconds=0)
    db['data_status'].create_index("date", unique=True)
    db['correlated_rule_ip'].create_index([("ip", 1), ("rule_id", 1), ("direction", 1)])
    db['correlated_rule_ip'].create_index("expires_at", expireAfterSeconds=0)
    db['correlated_rule_ports'].create_index([("rule_id", 1), ("port", 1)])
    db['correlated_rule_ports'].create_index("expires_at", expireAfterSeconds=0)

    # Initialize data_status from existing summaries
    logging.info("Initializing data_status from existing summaries...")
    pipeline = [
        {'$group': {
            '_id': '$date',
            'count': {'$sum': 1},
            'uploaded_at': {'$first': '$uploaded_at'}
        }}
    ]
    results = list(db['summaries'].aggregate(pipeline))
    for res in results:
        db['data_status'].update_one(
            {'date': res['_id']},
            {'$set': {
                'status': 'present',
                'count': res['count'],
                'uploaded_at': res['uploaded_at']
            }},
            upsert=True
        )

    # initialize correlated collections if empty
    if db['correlated_rule_ip'].count_documents({}) == 0 or db['correlated_rule_ports'].count_documents({}) == 0:
        build_correlations(db)

    logging.info(f"Database initialization complete.")
    return db


def build_correlations(db: pymongo.database.Database):
    logging.info("building correlated data from summaries...")

    # 1. IP + Rule + Direction correlation
    for direction in ['src', 'dst']:
        ip_field = '$src_ip' if direction == 'src' else '$dest_ip'
        pipeline_corr = [
            {'$group': {
                '_id': {'ip': ip_field, 'rule': '$rule', 'date': '$date'},
                'count': {'$sum': '$count'},
                'expires_at': {'$max': '$expires_at'}
            }},
            {'$group': {
                '_id': {'ip': '$_id.ip', 'rule': '$_id.rule'},
                'activity': {'$push': {'k': '$_id.date', 'v': '$count'}},
                'expires_at': {'$max': '$expires_at'}
            }}
        ]
        for item in db['summaries'].aggregate(pipeline_corr):
            activity_dict = {x['k']: x['v'] for x in item['activity']}
            db['correlated_rule_ip'].update_one(
                {
                    'ip': item['_id']['ip'],
                    'rule_id': item['_id']['rule'],
                    'direction': direction
                },
                {
                    '$set': {
                        'activity': activity_dict,
                        'expires_at': item['expires_at']
                    }
                },
                upsert=True
            )

    # 2. Rule + Port correlation
    pipeline_ports = [
        {'$unwind': '$ports'},
        {'$group': {
            '_id': {'rule': '$rule', 'port': '$ports', 'date': '$date'},
            'count': {'$sum': '$count'},
            'expires_at': {'$max': '$expires_at'}
        }},
        {'$group': {
            '_id': {'rule': '$_id.rule', 'port': '$_id.port'},
            'activity': {'$push': {'k': '$_id.date', 'v': '$count'}},
            'expires_at': {'$max': '$expires_at'}
        }}
    ]
    for item in db['summaries'].aggregate(pipeline_ports):
        activity_dict = {x['k']: x['v'] for x in item['activity']}
        db['correlated_rule_ports'].update_one(
            {
                'rule_id': item['_id']['rule'],
                'port': item['_id']['port']
            },
            {
                '$set': {
                    'activity': activity_dict,
                    'expires_at': item['expires_at']
                }
            },
            upsert=True
        )

    logging.info("Correlated data initialization complete")
